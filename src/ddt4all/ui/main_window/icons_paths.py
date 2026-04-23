ICON_AUTOREFRESH = ":icons/autorefresh.png"
ICON_BT = ":icons/bt.png"
ICON_COMMAND = ":icons/command.png"
ICON_DERELEK = ":icons/derelek.png"
ICON_DONATE = ":icons/donate.png"
ICON_DTC = ":icons/dtc.png"
ICON_ELS27 = ":icons/els27.png"
ICON_EXPERT = ":icons/expert.png"
ICON_EXPERT_B = ":icons/expert-b.png"
ICON_FLOWCONTROL = ":icons/flowcontrol.png"
ICON_HEX = ":icons/hex.png"
ICON_LOG = ":icons/log.png"
ICON_OBD = ":icons/obd.png"
ICON_OBDLINK = ":icons/obdlink.png"
ICON_SCAN = ":icons/scan.png"
ICON_USB = ":icons/usb.png"
ICON_WIFI = ":icons/wifi.png"
ICON_VGATE = ":icons/vgate.png"
ICON_VLINKER = ":icons/vlinker.png"
ICON_REFRESH = ":icons/refresh.png"
ICON_DOIP = ":icons/doip.png"

# vector_can.png is not compiled into the Qt resource bundle (pyrcc5 not run),
# so we load it from the file system directly.
from pathlib import Path as _Path
ICON_VECTOR = str(_Path(__file__).resolve().parent.parent.parent.parent.parent / "resources" / "icons" / "vector_can.png")