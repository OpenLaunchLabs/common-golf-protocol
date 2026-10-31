cmake_minimum_required(VERSION 3.16)

# The project targets the original ESP32 on ESP-WROOM-32 development boards.
set(COMPONENTS main)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(common_golf_demo VERSION 0.1.0)
