# External kernel-module build for the Robot Owl I2S sound card.
#
#   make            build owl_i2s.ko against the running kernel's headers
#   make install    build + install to /lib/modules/$(uname -r)/extra/robot-owl
#   make clean      remove build artifacts
#
# KDIR defaults to the running kernel's build dir; override it (KDIR=...) when
# cross-building against a specific vendor kernel tree.

KDIR ?= /lib/modules/$(shell uname -r)/build
PWD  := $(shell pwd)
MODDEST := /lib/modules/$(shell uname -r)/extra/robot-owl

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

install: all
	install -d $(MODDEST)
	install -m 644 owl_i2s.ko $(MODDEST)/
	depmod -a
