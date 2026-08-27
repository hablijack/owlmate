# Registriert src/ als Hauptkomponente fuer den espidf-Modus.
#
# WICHTIG: nur echte Quelldateien einsammeln. Das frueher hier stehende
# GLOB "src/*.*" wuerde inzwischen auch idf_component.yml als Quelldatei
# uebergeben, was CMake mit einem Fehler quittiert.
#
# Alle Diagnose-Sketche in src/ (dualtest, i2ctest, imuaxis, vibtest, camtest,
# powerprobe) sind per #if auf ihr jeweiliges *_ACTIVE-Flag geschuetzt und
# kompilieren im Firmware-Build zu nichts - sie duerfen also mitgeglobt werden.
# Neue Diagnose-Quellen MUESSEN diesem Muster folgen, sonst kollidieren ihre
# setup()/loop() mit denen aus main.cpp.
FILE(GLOB_RECURSE app_sources
     ${CMAKE_CURRENT_SOURCE_DIR}/*.c
     ${CMAKE_CURRENT_SOURCE_DIR}/*.cpp)

idf_component_register(SRCS ${app_sources}
                       INCLUDE_DIRS "." "../include")
