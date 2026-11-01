#pragma once
#include <cstdint>
#include <cmath>
#include <algorithm>
using std::isfinite;
#define F(x) x
#define constrain(x, lo, hi) std::max(float(lo), std::min(float(hi), x))
struct FakeSerial { void println(const char*) {} };
extern FakeSerial Serial;
