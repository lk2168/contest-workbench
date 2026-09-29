#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""exe 入口（PyInstaller 的构建目标就是这个文件）。

真正的逻辑在 `diansai_agent/launcher.py` —— 放在包里才能被测试导入，
这里只做一行转发，让打包工具有一个干净的顶层脚本。
"""
import sys

from diansai_agent.launcher import main

if __name__ == "__main__":
    sys.exit(main())
