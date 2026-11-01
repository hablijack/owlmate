#include "i2c_lock.h"

#if SERVO_MOTION_ENABLED
SemaphoreHandle_t i2cBusMutex() {
    static SemaphoreHandle_t m = nullptr;
    static portMUX_TYPE spin = portMUX_INITIALIZER_UNLOCKED;
    if (!m) {
        SemaphoreHandle_t fresh = xSemaphoreCreateMutex();   // Prioritaetsvererbung inklusive
        portENTER_CRITICAL(&spin);
        if (!m) { m = fresh; fresh = nullptr; }
        portEXIT_CRITICAL(&spin);
        if (fresh) vSemaphoreDelete(fresh);
    }
    return m;
}
#endif
