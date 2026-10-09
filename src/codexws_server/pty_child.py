"""Acquire a controlling terminal in a fresh child, then exec the target argv.

A separate exec avoids preexec_fn inside the multithreaded Web process.
"""
import fcntl
import os
import sys
import termios


def main():
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(0, os.getpgrp())
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)


if __name__ == "__main__":
    main()
