[app]
# title of your application
title = Universal RPA Studio
# project directory. the general assumption is that project_dir is the parent directory
# of input_file
project_dir = .
# source file path
input_file = src/universal_rpa/__main__.py
# directory where the executable output is generated
exec_directory = dist
# path to .pyproject project file
project_file = pyproject.toml
# application icon
icon =

[python]
# python path
python_path =
# python packages to install
packages = Nuitka==4.1.3
# buildozer = for deploying Android application
android_packages =

[qt]
# comma separated path to qml files required
# normally all the qml files required by the project are added automatically
qml_files =
# excluded qml plugin binaries
excluded_qml_plugins =
# path to required qt plugins
plugins =
# path to android rcc binary
android_rcc =
# comma separated list of modules to be included
modules = Core,Gui,Widgets

[android]
# path to pyside wheel
wheel_pyside =
# path to shiboken wheel
wheel_shiboken =
# plugins to be copied to libs folder of the packaged application.
plugins =

[nuitka]
# usage description for permissions requested by the app as found in the info.plist file
# of the app bundle
macos.permissions =
# mode = onefile/standalone
mode = standalone
# (str) specify any extra nuitka arguments
extra_args = --quiet --noinclude-qt-translations --windows-console-mode=disable --nofollow-import-to=tests --nofollow-import-to=samples --nofollow-import-to=scripts

[buildozer]
# build mode
mode = debug
# contrains path to pyside6 and shiboken6 recipe dir
recipe_dir =
# path to extra qt android jars to be loaded by the application
jars_dir =
# if empty uses default ndk path downloaded by buildozer
ndk_path =
# if empty uses default sdk path downloaded by buildozer
sdk_path =
# other libraries to be loaded. Comma separated.
local_libs =
# architecture of deployed platform
arch =
