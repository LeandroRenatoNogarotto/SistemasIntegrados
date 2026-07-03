#include <arpa/inet.h>
#include <cblas.h>
#include <netinet/in.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <cstddef>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

struct ModelConfig {
    std::string id;
    int rows;
    int cols;
    int width;
    int height;
    std::string h_path;
};

struct ModelData {
    ModelConfig config;
    std::vector<double> h;
    std::mutex lock;
    bool loaded = false;

    ModelData() = default;
    explicit ModelData(ModelConfig c) : config(std::move(c)) {}
    ModelData(const ModelData&) = delete;
    ModelData& operator=(const ModelData&) = delete;
};

struct Metrics {
    double wall_ms = 0.0;
    double cpu_ms = 0.0;
    long rss_kb = 0;
};

struct CgnrResult {
    std::vector<double> image;
    int iterations = 0;
    double error_abs = 0.0;
    double error_signed = 0.0;
    double residual_norm = 0.0;
    double lambda_value = 0.0;
    double reduction_factor_estimate = 0.0;
    Metrics metrics;
};

struct HttpRequest {
    std::string method;
    std::string path;
    std::unordered_map<std::string, std::string> headers;
    std::vector<char> body;
};

std::map<std::string, ModelData> models;
std::atomic<int> active_jobs{0};
std::atomic<int> completed_jobs{0};
std::atomic<int> rejected_jobs{0};
int max_workers = 1;

struct ActiveJobGuard {
    bool completed = false;

    ActiveJobGuard() {
        active_jobs++;
    }

    ~ActiveJobGuard() {
        active_jobs--;
        if (completed) {
            completed_jobs++;
        }
    }
};

std::string now_iso() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t time = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
    gmtime_r(&time, &tm);
    std::ostringstream out;
    out << std::put_time(&tm, "%Y-%m-%dT%H:%M:%SZ");
    return out.str();
}

long rss_kb() {
    rusage usage{};
    getrusage(RUSAGE_SELF, &usage);
    return usage.ru_maxrss;
}

double process_cpu_ms() {
    rusage usage{};
    getrusage(RUSAGE_SELF, &usage);
    return static_cast<double>(usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) * 1000.0 +
           static_cast<double>(usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1000.0;
}

std::vector<double> read_binary_vector(const std::string& path, std::size_t expected_size) {
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        throw std::runtime_error("cannot open " + path);
    }
    std::vector<double> data(expected_size);
    file.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(expected_size * sizeof(double)));
    if (file.gcount() != static_cast<std::streamsize>(expected_size * sizeof(double))) {
        throw std::runtime_error("unexpected file size: " + path);
    }
    return data;
}

ModelData& get_model(const std::string& model_id) {
    auto found = models.find(model_id);
    if (found == models.end()) {
        throw std::runtime_error("unknown model_id " + model_id);
    }
    ModelData& model = found->second;
    std::lock_guard<std::mutex> guard(model.lock);
    if (!model.loaded) {
        const auto size = static_cast<std::size_t>(model.config.rows) * static_cast<std::size_t>(model.config.cols);
        std::cout << "[C++ server] loading " << model.config.h_path << " (" << model.config.rows << "x" << model.config.cols << ")\n";
        model.h = read_binary_vector(model.config.h_path, size);
        model.loaded = true;
    }
    return model;
}

double norm2(const std::vector<double>& values) {
    return cblas_dnrm2(static_cast<int>(values.size()), values.data(), 1);
}

double dot(const std::vector<double>& a, const std::vector<double>& b) {
    return cblas_ddot(static_cast<int>(a.size()), a.data(), 1, b.data(), 1);
}

void mat_vec(const std::vector<double>& h, int rows, int cols, const std::vector<double>& x, std::vector<double>& y) {
    cblas_dgemv(CblasRowMajor, CblasNoTrans, rows, cols, 1.0, h.data(), cols, x.data(), 1, 0.0, y.data(), 1);
}

void transposed_mat_vec(const std::vector<double>& h, int rows, int cols, const std::vector<double>& x, std::vector<double>& y) {
    cblas_dgemv(CblasRowMajor, CblasTrans, rows, cols, 1.0, h.data(), cols, x.data(), 1, 0.0, y.data(), 1);
}

double estimate_reduction_factor(const std::vector<double>& h, int rows, int cols, int rounds = 8) {
    std::vector<double> v(cols, 1.0 / std::sqrt(static_cast<double>(cols)));
    std::vector<double> temp(rows, 0.0);
    std::vector<double> w(cols, 0.0);
    double estimate = 0.0;
    for (int i = 0; i < rounds; ++i) {
        mat_vec(h, rows, cols, v, temp);
        transposed_mat_vec(h, rows, cols, temp, w);
        estimate = norm2(w);
        if (estimate == 0.0) {
            return 0.0;
        }
        for (double& value : w) {
            value /= estimate;
        }
        v = w;
    }
    return estimate;
}

CgnrResult cgnr(const std::vector<double>& h, const std::vector<double>& g, int rows, int cols, int max_iterations, double tolerance) {
    const auto wall_start = std::chrono::steady_clock::now();
    const double cpu_start = process_cpu_ms();

    std::vector<double> f(cols, 0.0);
    std::vector<double> r = g;
    std::vector<double> z(cols, 0.0);
    std::vector<double> p(cols, 0.0);
    std::vector<double> z_next(cols, 0.0);
    std::vector<double> w(rows, 0.0);

    transposed_mat_vec(h, rows, cols, r, z);
    p = z;

    CgnrResult result;
    for (double value : z) {
        result.lambda_value = std::max(result.lambda_value, std::abs(value));
    }
    result.lambda_value *= 0.10;
    result.reduction_factor_estimate = estimate_reduction_factor(h, rows, cols);

    double previous_norm = norm2(r);
    result.error_abs = std::abs(previous_norm);
    result.error_signed = previous_norm;
    result.residual_norm = previous_norm;

    for (int i = 0; i < max_iterations; ++i) {
        mat_vec(h, rows, cols, p, w);
        const double z_norm_sq = dot(z, z);
        const double w_norm_sq = dot(w, w);
        if (z_norm_sq == 0.0 || w_norm_sq == 0.0) {
            break;
        }
        const double alpha = z_norm_sq / w_norm_sq;
        cblas_daxpy(cols, alpha, p.data(), 1, f.data(), 1);
        cblas_daxpy(rows, -alpha, w.data(), 1, r.data(), 1);
        transposed_mat_vec(h, rows, cols, r, z_next);

        const double current_norm = norm2(r);
        result.error_signed = current_norm - previous_norm;
        result.error_abs = std::abs(result.error_signed);
        result.residual_norm = current_norm;
        result.iterations = i + 1;

        const double beta = dot(z_next, z_next) / z_norm_sq;
        for (int j = 0; j < cols; ++j) {
            p[j] = z_next[j] + beta * p[j];
        }
        z = z_next;
        previous_norm = current_norm;
        if (result.error_abs < tolerance) {
            break;
        }
    }

    const auto wall_end = std::chrono::steady_clock::now();
    result.metrics.wall_ms = static_cast<double>(std::chrono::duration_cast<std::chrono::microseconds>(wall_end - wall_start).count()) / 1000.0;
    result.metrics.cpu_ms = process_cpu_ms() - cpu_start;
    result.metrics.rss_kb = rss_kb();
    result.image = std::move(f);
    return result;
}

void save_pgm_fortran(const std::string& path, const std::vector<double>& image, const ModelConfig& model, const CgnrResult& result, const std::string& job_id, const std::string& started_at, const std::string& ended_at) {
    const auto [min_it, max_it] = std::minmax_element(image.begin(), image.end());
    const double min_value = *min_it;
    const double max_value = *max_it;
    std::ofstream file(path, std::ios::binary);
    if (!file) {
        throw std::runtime_error("cannot write " + path);
    }
    file << "P5\n";
    file << "# algorithm=CGNR\n";
    file << "# language=C++\n";
    file << "# job_id=" << job_id << "\n";
    file << "# started_at=" << started_at << "\n";
    file << "# ended_at=" << ended_at << "\n";
    file << "# resolution=" << model.width << "x" << model.height << "\n";
    file << "# iterations=" << result.iterations << "\n";
    file << model.width << " " << model.height << "\n255\n";

    for (int y = 0; y < model.height; ++y) {
        for (int x = 0; x < model.width; ++x) {
            const int index = x * model.height + y;
            double scaled = 0.0;
            if (std::abs(max_value - min_value) > 1e-15) {
                scaled = (image[index] - min_value) * 255.0 / (max_value - min_value);
            }
            const auto pixel = static_cast<unsigned char>(std::clamp(scaled, 0.0, 255.0));
            file.write(reinterpret_cast<const char*>(&pixel), 1);
        }
    }
}

std::string header_value(const HttpRequest& request, const std::string& key) {
    auto found = request.headers.find(key);
    return found == request.headers.end() ? "" : found->second;
}

std::string trim(std::string value) {
    while (!value.empty() && (value.back() == '\r' || value.back() == '\n' || value.back() == ' ')) {
        value.pop_back();
    }
    while (!value.empty() && value.front() == ' ') {
        value.erase(value.begin());
    }
    return value;
}

HttpRequest read_request(int client_fd) {
    std::string raw;
    char buffer[8192];
    std::size_t header_end = std::string::npos;
    while ((header_end = raw.find("\r\n\r\n")) == std::string::npos) {
        const ssize_t n = recv(client_fd, buffer, sizeof(buffer), 0);
        if (n <= 0) {
            throw std::runtime_error("failed to read request");
        }
        raw.append(buffer, buffer + n);
    }

    HttpRequest request;
    std::istringstream headers(raw.substr(0, header_end));
    std::string request_line;
    std::getline(headers, request_line);
    std::istringstream request_line_stream(request_line);
    request_line_stream >> request.method >> request.path;

    std::string line;
    while (std::getline(headers, line)) {
        const auto pos = line.find(':');
        if (pos != std::string::npos) {
            std::string key = line.substr(0, pos);
            std::transform(key.begin(), key.end(), key.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
            request.headers[key] = trim(line.substr(pos + 1));
        }
    }

    const std::size_t content_length = header_value(request, "content-length").empty()
                                           ? 0
                                           : static_cast<std::size_t>(std::stoull(header_value(request, "content-length")));
    const std::size_t body_start = header_end + 4;
    request.body.assign(raw.begin() + static_cast<long>(body_start), raw.end());
    while (request.body.size() < content_length) {
        const ssize_t n = recv(client_fd, buffer, sizeof(buffer), 0);
        if (n <= 0) {
            throw std::runtime_error("failed to read request body");
        }
        request.body.insert(request.body.end(), buffer, buffer + n);
    }
    if (request.body.size() > content_length) {
        request.body.resize(content_length);
    }
    return request;
}

void send_response(int client_fd, int status, const std::string& body, const std::string& reason = "OK") {
    std::ostringstream response;
    response << "HTTP/1.1 " << status << " " << reason << "\r\n";
    response << "Content-Type: application/json\r\n";
    response << "Content-Length: " << body.size() << "\r\n";
    response << "Connection: close\r\n\r\n";
    response << body;
    const std::string text = response.str();
    send(client_fd, text.data(), text.size(), 0);
}

std::string status_json() {
    std::ostringstream out;
    out << "{";
    out << "\"server\":\"cpp\",";
    out << "\"active_jobs\":" << active_jobs.load() << ",";
    out << "\"completed_jobs\":" << completed_jobs.load() << ",";
    out << "\"rejected_jobs\":" << rejected_jobs.load() << ",";
    out << "\"max_workers\":" << max_workers << ",";
    out << "\"rss_kb\":" << rss_kb();
    out << "}";
    return out.str();
}

std::string reconstruct(const HttpRequest& request) {
    const std::string model_id = header_value(request, "x-model-id");
    if (model_id.empty()) {
        throw std::runtime_error("missing X-Model-Id header");
    }
    if (request.body.size() % sizeof(double) != 0) {
        throw std::runtime_error("request body must be f64 binary data");
    }
    if (active_jobs.load() >= max_workers) {
        rejected_jobs++;
        throw std::runtime_error("server saturated");
    }

    ActiveJobGuard active_guard;

    ModelData& model = get_model(model_id);
    const std::size_t count = request.body.size() / sizeof(double);
    if (count != static_cast<std::size_t>(model.config.rows)) {
        throw std::runtime_error("signal length does not match model rows");
    }
    std::vector<double> g(count);
    std::memcpy(g.data(), request.body.data(), request.body.size());

    const std::string job_id = header_value(request, "x-job-id").empty() ? std::to_string(std::chrono::system_clock::now().time_since_epoch().count()) : header_value(request, "x-job-id");
    const std::string started_at = now_iso();
    CgnrResult result = cgnr(model.h, g, model.config.rows, model.config.cols, 10, 0.0001);
    const std::string ended_at = now_iso();
    const std::string image_path = "outputs/cpp_server/" + job_id + "-" + model_id + ".pgm";
    std::filesystem::create_directories("outputs/cpp_server");
    save_pgm_fortran(image_path, result.image, model.config, result, job_id, started_at, ended_at);
    active_guard.completed = true;

    std::ostringstream out;
    out << std::fixed << std::setprecision(10);
    out << "{";
    out << "\"job_id\":\"" << job_id << "\",";
    out << "\"server\":\"cpp\",";
    out << "\"algorithm\":\"CGNR\",";
    out << "\"model_id\":\"" << model_id << "\",";
    out << "\"resolution\":[" << model.config.width << "," << model.config.height << "],";
    out << "\"iterations\":" << result.iterations << ",";
    out << "\"error_abs\":" << result.error_abs << ",";
    out << "\"error_signed\":" << result.error_signed << ",";
    out << "\"residual_norm\":" << result.residual_norm << ",";
    out << "\"lambda\":" << result.lambda_value << ",";
    out << "\"reduction_factor_estimate\":" << result.reduction_factor_estimate << ",";
    out << "\"queue_ms\":0,";
    out << "\"reconstruction_ms\":" << result.metrics.wall_ms << ",";
    out << "\"cpu_ms\":" << result.metrics.cpu_ms << ",";
    out << "\"rss_end_kb\":" << result.metrics.rss_kb << ",";
    out << "\"started_at\":\"" << started_at << "\",";
    out << "\"ended_at\":\"" << ended_at << "\",";
    out << "\"image_path\":\"" << image_path << "\"";
    out << "}";
    return out.str();
}

void handle_client(int client_fd) {
    try {
        const HttpRequest request = read_request(client_fd);
        if (request.method == "GET" && request.path == "/status") {
            send_response(client_fd, 200, status_json());
        } else if (request.method == "GET" && request.path == "/health") {
            send_response(client_fd, 200, "{\"ok\":true,\"server\":\"cpp\"}");
        } else if (request.method == "POST" && request.path == "/reconstruct") {
            send_response(client_fd, 200, reconstruct(request));
        } else {
            send_response(client_fd, 404, "{\"error\":\"not found\"}", "Not Found");
        }
    } catch (const std::exception& ex) {
        std::ostringstream body;
        body << "{\"error\":\"" << ex.what() << "\"}";
        send_response(client_fd, 503, body.str(), "Service Unavailable");
    }
    close(client_fd);
}

int main() {
    models.try_emplace("30x30", ModelConfig{"30x30", 27904, 900, 30, 30, "data/H-2.f64"});
    models.try_emplace("60x60", ModelConfig{"60x60", 50816, 3600, 60, 60, "data/H-1.f64"});
    // Workers derivados dos nucleos detectados (sem numero magico fixo):
    // usa max(2, nucleos - 1), permitindo override por CGNR_MAX_WORKERS.
    const unsigned int detected_threads = std::max(1u, std::thread::hardware_concurrency());
    unsigned int derived_workers = detected_threads > 1u ? detected_threads - 1u : 1u;
    derived_workers = std::max(2u, derived_workers);
    if (const char* env = std::getenv("CGNR_MAX_WORKERS")) {
        const int requested = std::atoi(env);
        if (requested > 0) {
            derived_workers = std::min(detected_threads, static_cast<unsigned int>(requested));
        }
    }
    max_workers = static_cast<int>(derived_workers);

    const int server_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (server_fd < 0) {
        throw std::runtime_error("failed to create socket");
    }
    int opt = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    address.sin_port = htons(8002);
    if (bind(server_fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
        throw std::runtime_error("failed to bind 0.0.0.0:8002");
    }
    if (listen(server_fd, 32) < 0) {
        throw std::runtime_error("failed to listen");
    }
    std::cout << "C++ CGNR server listening on http://0.0.0.0:8002 with max_workers=" << max_workers << "\n";

    while (true) {
        sockaddr_in client_addr{};
        socklen_t client_len = sizeof(client_addr);
        const int client_fd = accept(server_fd, reinterpret_cast<sockaddr*>(&client_addr), &client_len);
        if (client_fd >= 0) {
            std::thread(handle_client, client_fd).detach();
        }
    }
}
