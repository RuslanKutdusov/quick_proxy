# use only valid hostnames
PROXYMAPS = {
    #45067 : ("10.0.4.100", 45067),
    8001 : ("127.0.0.1", 8002, True),
    #8888 : ("10.0.4.100", 8888),
    #4001 : ("10.0.4.100", 4001),
    #4002 : ("10.0.4.100", 4002)
}

FILTER_WINDOW_SIZE = 64

FILTER_RE = [
  #  b"but.txt",
    b"pwd"
]