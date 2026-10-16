idf_component_register(SRCS "main.c" "display.c" "scene.c" "pet_art.c" "font.c" "font_data.c" "keys.c" "key_levels.c" "key_input.c" "console.c"
                    INCLUDE_DIRS "."
                    PRIV_REQUIRES esp_lcd esp_driver_spi esp_driver_ledc esp_adc esp_timer esp_driver_usb_serial_jtag vfs nvs_flash)
