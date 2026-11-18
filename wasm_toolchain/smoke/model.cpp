// A part model as a project may bring its own: C++20, throwing and catching an exception, linked against
// libc++ and libc++abi of the sysroot's exception-handling build into a reactor module (no main), as
// simee-core's simee_add_wasm_module builds one.
#include <algorithm>
#include <array>
#include <concepts>
#include <stdexcept>
#include <string>

namespace {

template <std::integral T>
T checked(T value) {
    if (value < 0) throw std::out_of_range("negative register value " + std::to_string(value));
    return value;
}

}  // namespace

extern "C" __attribute__((export_name("smoke_step"))) int smoke_step(int value) {
    std::array<int, 3> values{value, 3, value * 2};
    std::ranges::sort(values);
    try {
        return checked(values.front());
    } catch (const std::out_of_range&) {
        return -1;
    }
}
