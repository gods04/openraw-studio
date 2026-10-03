// OpenRAW's HE arithmetic, ahead-of-time counterpart of compiled_he*.py.
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <memory>
#include <stdexcept>

namespace {
using i64 = int64_t;
using i32 = int32_t;
constexpr Py_ssize_t max_samples = 64000000;

void require(bool condition, const char* message) {
    if (!condition) throw std::invalid_argument(message);
}

struct Buffer {
    Py_buffer view{};
    Buffer() = default;
    Buffer(const Buffer&) = delete;
    ~Buffer() { if (view.obj) PyBuffer_Release(&view); }

    void get(PyObject* object, const char* formats, int itemsize, int ndim, bool writable = false) {
        int flags = PyBUF_C_CONTIGUOUS | PyBUF_FORMAT | (writable ? PyBUF_WRITABLE : 0);
        if (PyObject_GetBuffer(object, &view, flags) < 0) throw std::invalid_argument("Invalid HE buffer");
        const char* format = view.format;
        if (format && (*format == '@' || *format == '=')) ++format;
        require(format && format[0] && !format[1] && std::strchr(formats, format[0]) &&
                view.itemsize == itemsize && view.ndim == ndim && view.len >= 0 &&
                view.len % itemsize == 0 &&
                reinterpret_cast<uintptr_t>(view.buf) % itemsize == 0, "Invalid HE buffer type or alignment");
        Py_ssize_t elements = 1;
        for (int axis = 0; axis < ndim; ++axis) {
            require(view.shape[axis] >= 0 && view.shape[axis] <= max_samples, "Invalid HE buffer dimensions");
            require(elements == 0 || view.shape[axis] <= max_samples / elements, "HE buffer exceeds sample limit");
            elements *= view.shape[axis];
        }
        require(elements == view.len / itemsize, "Invalid HE buffer length");
    }
    Py_ssize_t size() const { return view.len / view.itemsize; }
    template <typename T> T* data() const { return static_cast<T*>(view.buf); }
    bool overlaps(const Buffer& other) const {
        const auto a = reinterpret_cast<uintptr_t>(view.buf);
        const auto b = reinterpret_cast<uintptr_t>(other.view.buf);
        return view.len && other.view.len && (a <= b ? b - a < static_cast<uintptr_t>(view.len)
                                                    : a - b < static_cast<uintptr_t>(other.view.len));
    }
};

void distinct(const Buffer& output, std::initializer_list<const Buffer*> inputs) {
    for (const auto* input : inputs) require(!output.overlaps(*input), "HE output aliases an input or output");
}

struct ReleasedGIL {
    PyThreadState* state = PyEval_SaveThread();
    ~ReleasedGIL() { PyEval_RestoreThread(state); }
};

template <typename Function> PyObject* guarded(Function function) {
    try {
        function();
        Py_RETURN_NONE;
    } catch (const std::bad_alloc&) {
        return PyErr_NoMemory();
    } catch (const std::exception& error) {
        if (!PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, error.what());
        return nullptr;
    }
}

// Explicit floor division matches Python/NumPy for negative lifting values.
i64 shift(i64 value, int bits) {
    const i64 sign = -i64(value < 0);
    return ((value ^ sign) >> bits) ^ sign;
}

struct Bits {
    const uint8_t* bytes;
    Py_ssize_t length, position = 0;
    explicit Bits(const Buffer& source) : bytes(source.data<uint8_t>()), length(source.size() * 8) {}
    int read(int count) {
        require(count >= 0 && count <= length - position, "Truncated HE bitstream");
        int value = 0;
        for (int i = 0; i < count; ++i, ++position)
            value = (value << 1) | ((bytes[position / 8] >> (7 - position % 8)) & 1);
        return value;
    }
    void finish() {
        require(length - position <= 7, "Unexpected HE substream data");
        require(read(static_cast<int>(length - position)) == 0, "Nonzero HE substream padding");
    }
};

PyObject* decode_packet(PyObject*, PyObject* args) {
    PyObject *s, *c, *d, *z, *g, *t, *p, *l, *v;
    if (!PyArg_ParseTuple(args, "OOOOOOOOO", &s, &c, &d, &z, &g, &t, &p, &l, &v)) return nullptr;
    return guarded([&] {
        Buffer sig, code, data, signs, groups, thresholds, previous, lengths, values;
        sig.get(s, "B", 1, 1); code.get(c, "B", 1, 1); data.get(d, "B", 1, 1); signs.get(z, "B", 1, 1);
        groups.get(g, "lq", 8, 1); thresholds.get(t, "lq", 8, 1); previous.get(p, "B", 1, 1);
        lengths.get(l, "B", 1, 1, true); values.get(v, "il", 4, 1, true);
        require(groups.size() > 0 && groups.size() <= 26 && groups.size() == thresholds.size(), "Invalid HE packet bands");
        const auto* counts = groups.data<i64>();
        const auto* levels = thresholds.data<i64>();
        Py_ssize_t total = 0;
        for (Py_ssize_t band = 0; band < groups.size(); ++band) {
            require(counts[band] > 0 && counts[band] <= max_samples / 4 && levels[band] >= 0 && levels[band] <= 15,
                    "Invalid HE packet group or threshold");
            total += static_cast<Py_ssize_t>(counts[band]);
        }
        require(total <= max_samples / 4 && previous.size() == total && lengths.size() == total && values.size() == total * 4,
                "Invalid HE packet buffer lengths");
        distinct(lengths, {&sig, &code, &data, &signs, &groups, &thresholds, &previous, &values});
        distinct(values, {&sig, &code, &data, &signs, &groups, &thresholds, &previous});
        ReleasedGIL release;
        Bits sb(sig), cb(code), db(data), zb(signs);
        const auto* prior = previous.data<uint8_t>();
        auto* depths = lengths.data<uint8_t>();
        auto* coefficients = values.data<i32>();
        std::fill(coefficients, coefficients + total * 4, 0);
        Py_ssize_t offset = 0;
        for (Py_ssize_t band = 0; band < groups.size(); ++band) {
            const int threshold = static_cast<int>(levels[band]);
            int insignificant = 0;
            for (Py_ssize_t group = 0; group < counts[band]; ++group) {
                require(prior[offset + group] <= 15, "Invalid HE previous GCLI");
                if (group % 8 == 0) insignificant = sb.read(1);
                int residual = 0;
                if (!insignificant) {
                    while (cb.read(1)) {
                        require(++residual <= 31, "HE unary code exceeds the supported range");
                    }
                }
                const int baseline = std::max(threshold, int(prior[offset + group]));
                const int distance = baseline - threshold;
                const int depth = residual > 2 * distance ? threshold + residual :
                    residual % 2 ? baseline - (residual + 1) / 2 : baseline + residual / 2;
                require(depth >= 0 && depth <= 15, "HE GCLI value is outside the supported coefficient range");
                depths[offset + group] = static_cast<uint8_t>(depth);
                auto* coefficient = coefficients + (offset + group) * 4;
                for (int plane = depth - 1; plane >= threshold; --plane) {
                    const int nibble = db.read(4);
                    for (int channel = 0; channel < 4; ++channel)
                        coefficient[channel] |= ((nibble >> (3 - channel)) & 1) << plane;
                }
                for (int channel = 0; channel < 4; ++channel)
                    if (coefficient[channel] && zb.read(1)) coefficient[channel] *= -1;
            }
            offset += static_cast<Py_ssize_t>(counts[band]);
        }
        sb.finish(); cb.finish(); db.finish(); zb.finish();
    });
}

void dequantize(i64* output, const i32* values, const uint8_t* depths, int threshold, Py_ssize_t size) {
    for (Py_ssize_t i = 0; i < size; ++i) {
        const i64 value = values[i];
        require(value >= -32767 && value <= 32767 && depths[i / 4] <= 15, "Invalid HE wavelet coefficient");
        i64 term = value < 0 ? -value : value;
        i64 magnitude = term;
        const int bits = std::max(1, int(depths[i / 4]) - threshold + 1);
        for (term >>= bits; term; term >>= bits) magnitude += term;
        output[i] = (value < 0 ? -magnitude : magnitude) * 16;
    }
}

PyObject* horizontal(PyObject*, PyObject* args) {
    PyObject *v, *d, *t, *g, *o;
    if (!PyArg_ParseTuple(args, "OOOOO", &v, &d, &t, &g, &o)) return nullptr;
    return guarded([&] {
        Buffer values, depths, thresholds, groups, output;
        values.get(v, "il", 4, 1); depths.get(d, "B", 1, 1); thresholds.get(t, "lq", 8, 1);
        groups.get(g, "lq", 8, 1); output.get(o, "il", 4, 2, true);
        const Py_ssize_t count = output.view.shape[1];
        require(output.view.shape[0] == 8 && count >= 32 && count <= 32764 && count % 4 == 0 &&
                groups.size() == 26 && thresholds.size() == 26, "Invalid HE horizontal dimensions");
        const auto* counts = groups.data<i64>();
        const auto* levels = thresholds.data<i64>();
        Py_ssize_t total = 0;
        for (int band = 0; band < 26; ++band) {
            require(counts[band] > 0 && counts[band] <= count && levels[band] >= 0 && levels[band] <= 15,
                    "Invalid HE horizontal band");
            total += static_cast<Py_ssize_t>(counts[band]);
        }
        require(depths.size() == total && values.size() == total * 4, "Invalid HE horizontal buffer lengths");
        distinct(output, {&values, &depths, &thresholds, &groups});
        ReleasedGIL release;
        std::unique_ptr<i64[]> low(new i64[count]), work(new i64[count]), high(new i64[count / 2]);
        Py_ssize_t sizes[5], offset = 0;
        int band = 0, row = 0;
        const auto read_band = [&](i64* destination, Py_ssize_t size) {
            require(counts[band] == (size + 3) / 4, "HE band size does not match width");
            dequantize(destination, values.data<i32>() + offset * 4, depths.data<uint8_t>() + offset,
                       static_cast<int>(levels[band]), size);
            offset += static_cast<Py_ssize_t>(counts[band++]);
        };
        for (int level_count : {5, 5, 0, 5, 1, 1, 0, 1}) {
            Py_ssize_t size = count;
            for (int level = 0; level < level_count; ++level) {
                sizes[level] = size / 2;
                size -= size / 2;
            }
            read_band(low.get(), size);
            for (int level = level_count - 1; level >= 0; --level) {
                const Py_ssize_t high_size = sizes[level];
                read_band(high.get(), high_size);
                for (Py_ssize_t col = 0; col < size; ++col) {
                    const auto left = std::max(Py_ssize_t(0), col - 1), right = std::min(col, high_size - 1);
                    work[2 * col] = low[col] - shift(high[left] + high[right] + 2, 2);
                }
                for (Py_ssize_t col = 0; col < high_size; ++col) {
                    const auto right = std::min(col + 1, size - 1);
                    work[2 * col + 1] = high[col] + shift(work[2 * col] + work[2 * right], 1);
                }
                low.swap(work);
                size += high_size;
            }
            for (Py_ssize_t col = 0; col < count; ++col) output.data<i32>()[row * count + col] = static_cast<i32>(low[col]);
            ++row;
        }
    });
}

PyObject* linear_color(PyObject*, PyObject* args) {
    PyObject *c, *t, *o;
    if (!PyArg_ParseTuple(args, "OOO", &c, &t, &o)) return nullptr;
    return guarded([&] {
        Buffer components, curve, output;
        components.get(c, "il", 4, 3); curve.get(t, "H", 2, 1); output.get(o, "H", 2, 2, true);
        const Py_ssize_t height = components.view.shape[1], width = components.view.shape[2];
        require(components.view.shape[0] == 4 && height > 0 && width > 0 && curve.size() == 65536 &&
                output.view.shape[0] == 2 * height && output.view.shape[1] == 2 * width,
                "Invalid HE color dimensions");
        distinct(output, {&components, &curve});
        ReleasedGIL release;
        const Py_ssize_t size = height * width;
        const auto* planes = components.data<i32>();
        const auto component = [&](int plane, Py_ssize_t row, Py_ssize_t col) -> i64 {
            return planes[plane * size + row * width + col];
        };
        const auto lookup = [&](i64 value) { return curve.data<uint16_t>()[std::min(i64(65535), std::max(i64(0), value + 32768))]; };
        std::unique_ptr<i64[]> luma(new i64[size]), green1(new i64[size]), green2(new i64[size]);
        for (Py_ssize_t row = 0; row < height; ++row) {
            const auto up = std::max(Py_ssize_t(0), row - 1);
            for (Py_ssize_t col = 0; col < width; ++col) {
                const auto right = std::min(width - 1, col + 1), index = row * width + col;
                const auto delta = component(2, row, col) + component(2, row, right) + component(2, up, col) + component(2, up, right);
                luma[index] = component(0, row, col) - shift(delta, 3);
                const auto chroma = component(1, row, col) + component(1, row, right) + component(3, row, col) + component(3, up, col);
                green1[index] = luma[index] - shift(chroma, 3);
            }
        }
        for (Py_ssize_t row = 0; row < height; ++row) {
            const auto down = std::min(height - 1, row + 1);
            for (Py_ssize_t col = 0; col < width; ++col) {
                const auto left = std::max(Py_ssize_t(0), col - 1), index = row * width + col;
                i64 below, below_left;
                if (row + 1 < height) {
                    below = luma[down * width + col]; below_left = luma[down * width + left];
                } else {
                    const auto right = std::min(width - 1, col + 1), left_right = std::min(width - 1, left + 1);
                    // Extend source components before reconstructing the virtual bottom row.
                    below = component(0, row, col) - shift(component(2, row, col) + component(2, row, right), 2);
                    below_left = component(0, row, left) - shift(component(2, row, left) + component(2, row, left_right), 2);
                }
                const auto second_luma = component(2, row, col) + shift(luma[index] + luma[row * width + left] + below + below_left, 2);
                const auto chroma = component(1, row, col) + component(1, down, col) + component(3, row, col) + component(3, row, left);
                green2[index] = second_luma - shift(chroma, 3);
            }
        }
        for (Py_ssize_t row = 0; row < height; ++row) {
            const auto up = std::max(Py_ssize_t(0), row - 1), down = std::min(height - 1, row + 1);
            for (Py_ssize_t col = 0; col < width; ++col) {
                const auto left = std::max(Py_ssize_t(0), col - 1), right = std::min(width - 1, col + 1), index = row * width + col;
                const auto red = component(1, row, col) + shift(green1[index] + green1[row * width + left] + green2[index] + green2[up * width + col], 2);
                const auto blue = component(3, row, col) + shift(green1[index] + green1[down * width + col] + green2[index] + green2[row * width + right], 2);
                auto* destination = output.data<uint16_t>() + 4 * row * width + 2 * col;
                destination[0] = lookup(red); destination[1] = lookup(green1[index]);
                destination[2 * width] = lookup(green2[index]); destination[2 * width + 1] = lookup(blue);
            }
        }
    });
}

PyMethodDef methods[] = {
    {"decode_packet", decode_packet, METH_VARARGS, "Decode one bounded HE entropy packet into caller-owned arrays."},
    {"horizontal", horizontal, METH_VARARGS, "Synthesize eight HE horizontal rows into a caller-owned array."},
    {"linear_color", linear_color, METH_VARARGS, "Reconstruct and linearize one bounded HE color tile."},
    {nullptr, nullptr, 0, nullptr}
};
PyModuleDef module = {PyModuleDef_HEAD_INIT, "_he_cpu", nullptr, -1, methods};
}  // namespace

PyMODINIT_FUNC PyInit__he_cpu() { return PyModule_Create(&module); }
