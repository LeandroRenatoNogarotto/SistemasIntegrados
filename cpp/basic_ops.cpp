#include <cblas.h>

#include <cmath>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

struct Matrix {
    int rows = 0;
    int cols = 0;
    std::vector<double> values;
};

Matrix read_semicolon_matrix(const std::string& path) {
    std::ifstream file(path);
    if (!file) {
        throw std::runtime_error("Cannot open " + path);
    }

    Matrix matrix;
    std::string line;
    while (std::getline(file, line)) {
        if (line.empty()) {
            continue;
        }
        std::vector<double> row;
        std::stringstream ss(line);
        std::string value;
        while (std::getline(ss, value, ';')) {
            row.push_back(std::stod(value));
        }
        if (matrix.cols == 0) {
            matrix.cols = static_cast<int>(row.size());
        } else if (matrix.cols != static_cast<int>(row.size())) {
            throw std::runtime_error("Inconsistent column count in " + path);
        }
        matrix.values.insert(matrix.values.end(), row.begin(), row.end());
        matrix.rows++;
    }
    return matrix;
}

double max_abs_diff(const std::vector<double>& a, const std::vector<double>& b) {
    if (a.size() != b.size()) {
        throw std::runtime_error("Different vector sizes");
    }
    double diff = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) {
        diff = std::max(diff, std::abs(a[i] - b[i]));
    }
    return diff;
}

void assert_close(const std::string& name, const std::vector<double>& actual, const std::vector<double>& expected, double tolerance) {
    const double diff = max_abs_diff(actual, expected);
    if (diff > tolerance) {
        throw std::runtime_error(name + " failed; max diff=" + std::to_string(diff));
    }
    std::cout << "OK " << name << " max_diff=" << diff << "\n";
}

int main(int argc, char** argv) {
    try {
        if (argc != 2) {
            throw std::runtime_error("Usage: basic_ops_cpp <Dados directory>");
        }
        const std::string base = argv[1];
        const Matrix m = read_semicolon_matrix(base + "/M.csv");
        const Matrix n = read_semicolon_matrix(base + "/N.csv");
        const Matrix mn_expected = read_semicolon_matrix(base + "/MN.csv");
        const Matrix a = read_semicolon_matrix(base + "/a.csv");
        const Matrix am_expected = read_semicolon_matrix(base + "/aM.csv");

        std::vector<double> mn(m.rows * n.cols, 0.0);
        cblas_dgemm(
            CblasRowMajor,
            CblasNoTrans,
            CblasNoTrans,
            m.rows,
            n.cols,
            m.cols,
            1.0,
            m.values.data(),
            m.cols,
            n.values.data(),
            n.cols,
            0.0,
            mn.data(),
            n.cols);
        assert_close("MN = M * N", mn, mn_expected.values, 1e-9);

        std::vector<double> am(a.rows * m.cols, 0.0);
        cblas_dgemm(
            CblasRowMajor,
            CblasNoTrans,
            CblasNoTrans,
            a.rows,
            m.cols,
            a.cols,
            1.0,
            a.values.data(),
            a.cols,
            m.values.data(),
            m.cols,
            0.0,
            am.data(),
            m.cols);
        assert_close("aM = a * M", am, am_expected.values, 5e-3);

        std::vector<double> ma(m.rows * a.rows, 0.0);
        cblas_dgemm(
            CblasRowMajor,
            CblasNoTrans,
            CblasTrans,
            m.rows,
            a.rows,
            m.cols,
            1.0,
            m.values.data(),
            m.cols,
            a.values.data(),
            a.cols,
            0.0,
            ma.data(),
            a.rows);
        // Ma = M * a (matriz por vetor). Sem arquivo de referencia no Dados,
        // validamos contra a identidade auto-verificavel: ma[i] = sum_j M[i,j]*a[j].
        std::vector<double> ma_manual(m.rows * a.rows, 0.0);
        for (int i = 0; i < m.rows; ++i) {
            double acc = 0.0;
            for (int j = 0; j < m.cols; ++j) {
                acc += m.values[i * m.cols + j] * a.values[j];
            }
            ma_manual[i] = acc;
        }
        assert_close("Ma = M * a^T", ma, ma_manual, 1e-9);

        std::vector<double> scalar_left = m.values;
        std::vector<double> scalar_right = m.values;
        const double scalar = a.values[0];
        cblas_dscal(static_cast<int>(scalar_left.size()), scalar, scalar_left.data(), 1);
        for (double& value : scalar_right) {
            value *= scalar;
        }
        assert_close("a0M = a0 * M", scalar_left, scalar_right, 1e-12);
    } catch (const std::exception& ex) {
        std::cerr << "ERROR: " << ex.what() << "\n";
        return 1;
    }
    return 0;
}
