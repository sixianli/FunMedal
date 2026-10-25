idf_component_register(SRCS "main.c" "display.c" "scene.c" "font.c" "font_data.c" "keys.c" "key_levels.c" "key_input.c" "console.c" "ble_link.c" "audio.c" "link_version.c" "rows.c" "draw.c" "dirty.c" "screen.c" "theme_board.c" "theme_arcade.c" "theme_comic.c" "theme_tower.c" "character.c" "character_art.c" "character_data.c"
                    INCLUDE_DIRS "."
                    PRIV_REQUIRES esp_lcd esp_driver_spi esp_driver_ledc esp_adc esp_timer esp_driver_usb_serial_jtag esp_driver_i2c esp_driver_i2s vfs nvs_flash bt)

idf_build_get_property(python PYTHON)
set(font_dir "${CMAKE_BINARY_DIR}/fonts")
set(font_tool "${COMPONENT_DIR}/../../tools/gen_fonts.py")
set(font_names large text pixel)
set(font_files "")
foreach(name ${font_names})
    list(APPEND font_files "${font_dir}/font_${name}.bin")
endforeach()
file(GLOB font_source_files "${COMPONENT_DIR}/*.c")
set(font_inputs
    "${font_tool}"
    "${COMPONENT_DIR}/../../tools/gen_font.swift"
    "${COMPONENT_DIR}/../../third_party/noto-sans-sc/NotoSansSC-Regular.otf"
    "${COMPONENT_DIR}/../../third_party/fusion-pixel/fusion-pixel-12px-proportional-zh_hans.bdf"
    "${COMPONENT_DIR}/../../third_party/zcool-kuaile/ZCOOLKuaiLe-Regular.ttf"
    ${font_source_files})
add_custom_command(OUTPUT ${font_files}
                   COMMAND ${python} "${font_tool}" --out "${font_dir}"
                   DEPENDS ${font_inputs}
                   VERBATIM)
add_custom_target(fonts DEPENDS ${font_files})
foreach(file ${font_files})
    target_add_binary_data(${COMPONENT_LIB} "${file}" BINARY DEPENDS fonts)
endforeach()

if(NOT FUNMEDAL_ASSET_PACK)
    set(FUNMEDAL_ASSET_PACK "${COMPONENT_DIR}/../../assets/default")
endif()
get_filename_component(asset_pack "${FUNMEDAL_ASSET_PACK}" ABSOLUTE BASE_DIR "${COMPONENT_DIR}/../..")
set(asset_dir "${CMAKE_BINARY_DIR}/assets")
set(asset_file "${asset_dir}/character.bin")
file(GLOB asset_pngs "${asset_pack}/character/*.png")
add_custom_command(OUTPUT "${asset_file}"
                   COMMAND ${python} "${COMPONENT_DIR}/../../tools/gen_assets.py" --pack "${asset_pack}" --out "${asset_dir}"
                   DEPENDS "${COMPONENT_DIR}/../../tools/gen_assets.py" "${COMPONENT_DIR}/../../tools/pngio.py" ${asset_pngs}
                   VERBATIM)
add_custom_target(assets DEPENDS "${asset_file}")
target_add_binary_data(${COMPONENT_LIB} "${asset_file}" BINARY DEPENDS assets)
message(STATUS "FunMedal character pack: ${asset_pack}")
