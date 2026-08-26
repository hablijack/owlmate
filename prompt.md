This codebase does not work at the moment. I have a problem with the two Waveshare 0.71" LCD displays (https://www.waveshare.com/wiki/0.71inch_LCD_Module) that share the SPI-bus. They work in isolation but together they do not work. I have everything connected to my xiao esp32-s3 sense (https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/) and checked every wiring lane with a multimeter - no shortage to ground and all connections work from board to LCD. It must be something with the shared SPI-bus and the init of the displays. The previous LLM-coding-model spent a day on trying things, so the codebase needs a refactoring too of all the tries. Here is the concrete SPI wiring that is current applied to my board and displays: 

# left eye

|Display-Connector-Name|Cable-Color|ESP32-port|shared (means both displays connect to the same port)|
|VCC|red|3.3V|yes|
|GND|black|Ground|yes|
|DIN|white|D8|yes|
|CLK|yellow|D4|yes|
|CS|orange|D7|no|
|DC|green|D10|no|
|RST|blue|D6|yes|
|BL|purple|3.3V|yes (same 3.3V as VCC)|

# right eye

|Display-Connector-Name|Cable-Color|ESP32-port|shared (means both displays connect to the same port)|
|VCC|red|3.3V|yes|
|GND|black|Ground|yes|
|DIN|white|D8|yes|
|CLK|yellow|D4|yes|
|CS|orange|D9|no|
|DC|green|D3|no|
|RST|blue|D6|yes|
|BL|purple|3.3V|yes (same 3.3V as VCC)|

Focus on fixing the SPI-Bus and ignore all the other peripherals. The ESP32 is booting with that firmware and we get a result in serial terminal. You can utilize everything needed, to debug the problem. Access USB ports, read Serial, compile with PIO, use the esptools installed. If something is missing, install it on my macbook.
Goal of this session is that both displays are useable with the shared SPI-bus and can display different images.
If you have questions or something is unclear, ask me about cabling or connections. Don't trust the existing codebase, as told it is from a weak LLM-coding model and may contain bugs and glitches!