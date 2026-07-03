#include <cblas.h>
#include <sys/resource.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

struct Metrics {
    double wall_ms = 0.0;
    double cpu_ms = 0.0;
    long rss_kb = 0;
};

struct Timer {
    std::chrono::steady_clock::time_point wall_start;
    rusage usage_start{};

    static Timer start() {
        Timer timer;
        timer.wall_start = std::chrono::steady_clock::now();
        getrusage(RUSAGE_SELF, &timer.usage_start);
        return timer;
    }

    Metrics stop() const {
        rusage usage_end{};
        getrusage(RUSAGE_SELF, &usage_end);
        const auto wall_end = std::chrono::steady_clock::now();
        const auto wall_us = std::chrono::duration_cast<std::chrono::microseconds>(wall_end - wall_start).count();
        const double start_cpu =
            static_cast<double>(usage_start.ru_utime.tv_sec + usage_start.ru_stime.tv_sec) * 1000.0 +
            static_cast<double>(usage_start.ru_utime.tv_usec + usage_start.ru_stime.tv_usec) / 1000.0;
        const double end_cpu =
            static_cast<double>(usage_end.ru_utime.tv_sec + usage_end.ru_stime.tv_sec) * 1000.0 +
            static_cast<double>(usage_end.ru_utime.tv_usec + usage_end.ru_stime.tv_usec) / 1000.0;
        return Metrics{static_cast<double>(wall_us) / 1000.0, end_cpu - start_cpu, usage_end.ru_maxrss};
    }
};

struct Result {
    std::vector<double> image;
    int iterations = 0;
    double error_abs = 0.0;
    double error_signed = 0.0;
    double residual_norm = 0.0;
    double lambda_value = 0.0;
    double reduction_factor_estimate = 0.0;
    Metrics metrics;
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

std::string get_arg(const std::unordered_map<std::string, std::string>& args, const std::string& key) {
    const auto found = args.find(key);
    if (found == args.end()) {
        throw std::runtime_error("Missing argument: " + key);
    }
    return found->second;
}

std::unordered_map<std::string, std::string> parse_args(int argc, char** argv) {
    std::unordered_map<std::string, std::string> args;
    for (int i = 1; i < argc; i += 2) {
        if (i + 1 >= argc) {
            throw std::runtime_error("Argument without value: " + std::string(argv[i]));
        }
        args[argv[i]] = argv[i + 1];
    }
    return args;
}

std::vector<double> read_binary_vector(const std::string& path, std::size_t expected_size) {
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        throw std::runtime_error("Cannot open " + path);
    }
    std::vector<double> data(expected_size);
    file.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(expected_size * sizeof(double)));
    if (file.gcount() != static_cast<std::streamsize>(expected_size * sizeof(double))) {
        throw std::runtime_error("Unexpected file size: " + path);
    }
    return data;
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

Result cgnr(const std::vector<double>& h, const std::vector<double>& g, int rows, int cols, int max_iterations, double tolerance) {
    const Timer timer = Timer::start();
    std::vector<double> f(cols, 0.0);
    std::vector<double> r = g;
    std::vector<double> z(cols, 0.0);
    std::vector<double> p(cols, 0.0);
    std::vector<double> z_next(cols, 0.0);
    std::vector<double> w(rows, 0.0);

    transposed_mat_vec(h, rows, cols, r, z);
    p = z;

    double max_abs = 0.0;
    for (double value : z) {
        max_abs = std::max(max_abs, std::abs(value));
    }

    Result result;
    result.lambda_value = max_abs * 0.10;
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

    result.metrics = timer.stop();
    result.image = std::move(f);
    return result;
}

void save_pgm(const std::string& path, const std::vector<double>& image, int width, int height, const Result& result, const std::string& started_at, const std::string& ended_at) {
    const auto [min_it, max_it] = std::minmax_element(image.begin(), image.end());
    const double min_value = *min_it;
    const double max_value = *max_it;
    std::ofstream file(path, std::ios::binary);
    if (!file) {
        throw std::runtime_error("Cannot write " + path);
    }

    file << "P5\n";
    file << "# algorithm=CGNR\n";
    file << "# language=C++\n";
    file << "# started_at=" << started_at << "\n";
    file << "# ended_at=" << ended_at << "\n";
    file << "# resolution=" << width << "x" << height << "\n";
    file << "# iterations=" << result.iterations << "\n";
    file << width << " " << height << "\n255\n";

    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            const int index = x * height + y;
            double scaled = 0.0;
            if (std::abs(max_value - min_value) > 1e-15) {
                scaled = (image[index] - min_value) * 255.0 / (max_value - min_value);
            }
            const auto pixel = static_cast<unsigned char>(std::clamp(scaled, 0.0, 255.0));
            file.write(reinterpret_cast<const char*>(&pixel), 1);
        }
    }
}

void save_json(const std::string& path, const Result& result, int rows, int cols, int width, int height, const std::string& started_at, const std::string& ended_at) {
    std::ofstream file(path);
    if (!file) {
        throw std::runtime_error("Cannot write " + path);
    }
    file << std::fixed << std::setprecision(10);
    file << "{\n";
    file << "  \"algorithm\": \"CGNR\",\n";
    file << "  \"language\": \"C++\",\n";
    file << "  \"h_shape\": [" << rows << ", " << cols << "],\n";
    file << "  \"resolution\": \"" << width << "x" << height << "\",\n";
    file << "  \"started_at\": \"" << started_at << "\",\n";
    file << "  \"ended_at\": \"" << ended_at << "\",\n";
    file << "  \"iterations\": " << result.iterations << ",\n";
    file << "  \"error_abs\": " << result.error_abs << ",\n";
    file << "  \"error_signed\": " << result.error_signed << ",\n";
    file << "  \"residual_norm\": " << result.residual_norm << ",\n";
    file << "  \"lambda\": " << result.lambda_value << ",\n";
    file << "  \"reduction_factor_estimate\": " << result.reduction_factor_estimate << ",\n";
    file << "  \"metrics\": {\n";
    file << "    \"wall_ms\": " << result.metrics.wall_ms << ",\n";
    file << "    \"cpu_ms\": " << result.metrics.cpu_ms << ",\n";
    file << "    \"max_rss_kb\": " << result.metrics.rss_kb << "\n";
    file << "  }\n";
    file << "}\n";
}

int main(int argc, char** argv) {
    try {
        const auto args = parse_args(argc, argv);
        const std::string h_path = get_arg(args, "--h");
        const std::string g_path = get_arg(args, "--g");
        const int rows = std::stoi(get_arg(args, "--rows"));
        const int cols = std::stoi(get_arg(args, "--cols"));
        const int width = std::stoi(get_arg(args, "--width"));
        const int height = std::stoi(get_arg(args, "--height"));
        const int max_iterations = std::stoi(get_arg(args, "--max-iterations"));
        const double tolerance = std::stod(get_arg(args, "--tolerance"));
        const std::string image_path = get_arg(args, "--image-out");
        const std::string json_path = get_arg(args, "--json-out");

        const auto h = read_binary_vector(h_path, static_cast<std::size_t>(rows) * static_cast<std::size_t>(cols));
        const auto g = read_binary_vector(g_path, static_cast<std::size_t>(rows));
        const std::string started_at = now_iso();
        const auto result = cgnr(h, g, rows, cols, max_iterations, tolerance);
        const std::string ended_at = now_iso();

        save_pgm(image_path, result.image, width, height, result, started_at, ended_at);
        save_json(json_path, result, rows, cols, width, height, started_at, ended_at);

        std::cout << "CGNR C++ OK iterations=" << result.iterations
                  << " wall_ms=" << result.metrics.wall_ms
                  << " cpu_ms=" << result.metrics.cpu_ms
                  << " rss_kb=" << result.metrics.rss_kb << "\n";
    } catch (const std::exception& ex) {
        std::cerr << "ERROR: " << ex.what() << "\n";
        return 1;
    }
    return 0;
}
