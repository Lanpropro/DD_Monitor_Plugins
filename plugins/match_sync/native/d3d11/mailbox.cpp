#define WIN32_LEAN_AND_MEAN
#include <windows.h>

extern "C" __declspec(dllexport) LONG swap_slot(volatile LONG* middle, LONG slot) {
    return InterlockedExchange(middle, slot);
}
