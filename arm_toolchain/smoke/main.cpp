// Links against libstdc++'s headers and newlib, as simee's RP2040 firmware may: no exceptions or RTTI.
#include <algorithm>
#include <array>

volatile int seed = 7;

int main() {
    std::array<int, 4> values{seed, 3, seed * 2, 1};
    std::sort(values.begin(), values.end());
    return values.front();
}
