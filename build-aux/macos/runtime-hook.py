import os
import sys

# PyInstaller's _MEIPASS points to the app's resource directory
meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))

lib_dir = os.path.join(meipass, 'lib')
share_dir = os.path.join(meipass, 'share')

if os.path.isdir(lib_dir):
    os.environ.setdefault('DYLD_LIBRARY_PATH', lib_dir)
    os.environ.setdefault('GST_PLUGIN_PATH',
                          os.path.join(lib_dir, 'gstreamer-1.0'))

# PyInstaller's gi hook places .typelib files in <meipass>/gi_typelibs
typelib_dir = os.path.join(meipass, 'gi_typelibs')
if os.path.isdir(typelib_dir):
    os.environ.setdefault('GI_TYPELIB_PATH', typelib_dir)
