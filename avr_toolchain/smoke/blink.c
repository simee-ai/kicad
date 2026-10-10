#ifndef F_CPU
#define F_CPU 16000000UL
#endif
#include <avr/io.h>
#include <util/delay.h>
int main(void) {
    DDRD |= (1 << PD2);
    for (;;) {
        PORTD ^= (1 << PD2);
        _delay_ms(500);
    }
}
