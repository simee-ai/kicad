/* Links against newlib (memset, strlen) and libgcc (the M0+ has no divide instruction). */
#include <string.h>

volatile unsigned divisor = 3;
char buffer[16];

int main(void) {
    memset(buffer, 'x', sizeof buffer - 1);
    return (int)(strlen(buffer) / divisor);
}
