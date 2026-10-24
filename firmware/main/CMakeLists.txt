idf_component_register(SRCS "main.c" "display.c" "scene.c" "pet_art.c" "font.c" "font_large.c" "font_text.c" "keys.c" "key_levels.c" "key_input.c" "console.c" "ble_link.c" "audio.c" "link_version.c" "rows.c"
                    INCLUDE_DIRS "."
                    PRIV_REQUIRES esp_lcd esp_driver_spi esp_driver_ledc esp_adc esp_timer esp_driver_usb_serial_jtag esp_driver_i2c esp_driver_i2s vfs nvs_flash bt)
