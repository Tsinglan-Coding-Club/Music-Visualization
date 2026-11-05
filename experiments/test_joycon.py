#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ↑ 上一行声明"用 python3 解释执行 + 文件用 UTF-8 编码"，避免中文注释在 macOS 上乱码

"""
Joy-Con 直连 Mac 的测试脚本（v3：去掉冗余小函数，全部逻辑集中在 main 里）

Joy-Con 以 "Nintendo Switch Joy-Con (L/R)" 组合设备接入时，SDL 会动态增删设备实例，
pygame.event.get() 内部按实例 ID 索引会抛 KeyError → SystemError，
所以这里只做轮询，事件接口降级为"尽力检测断开"。

准备工作：系统设置 → 蓝牙 → 按住 Joy-Con 侧面 SYNC 键 3 秒 → 灯快闪 → 点连接

运行：
    .venv/bin/python test_joycon.py          # 监控第 0 个手柄
    .venv/bin/python test_joycon.py 1        # 监控第 1 个
    .venv/bin/python test_joycon.py --list   # 只列信息后退出

退出：Ctrl + C
"""

import sys                                       # 读命令行参数、设置退出码
import time                                      # 计时 + 限速，避免空转吃满 CPU

import pygame                                    # SDL2 的 Python 封装，内置 Switch 手柄驱动
pygame.init()                                    # 初始化 SDL 各子系统
pygame.joystick.init()                           # 单独初始化手柄模块，不做这步 get_count() 恒为 0

# 轴命名表：组合模式下 SDL 报 6 个轴，前四个是两个摇杆，后两个随固件不同而不同
AXIS_LABELS = {
    0: "左摇杆X",      # 左摇杆水平，左负右正
    1: "左摇杆Y",      # 左摇杆垂直，上负下正
    2: "右摇杆X",      # 右摇杆水平
    3: "右摇杆Y",      # 右摇杆垂直
}

# 按键命名表：SDL 对 Switch 手柄的默认映射；组合模式下索引会偏移，所以打印时带原始编号
BUTTON_LABELS = {
    0: "B", 1: "A", 2: "Y", 3: "X",              # 面键，注意 Switch 的 A/B、X/Y 与 Xbox 相反
    4: "L", 5: "R", 6: "ZL", 7: "ZR",            # 肩键与扳机
    8: "Minus", 9: "Plus",                       # 减号 / 加号
    10: "左摇杆按下", 11: "右摇杆按下",            # L3 / R3
    12: "Home", 13: "Capture",                   # 房子键 / 截图键
    14: "SL", 15: "SR",                          # 轨道侧键
}


def main():                                      # 全部逻辑集中在这一个函数里
    argv = sys.argv[1:]                          # 取命令行参数，跳过脚本名

    count = pygame.joystick.get_count()          # 问 SDL 当前识别到几个手柄
    print(f"检测到 {count} 个手柄设备\n")         # 0 就说明没连上
    for i in range(count):                       # 逐个打印身份信息
        js = pygame.joystick.Joystick(i)         # 按编号取手柄对象
        js.init()                                # 必须 init 才能读属性
        print(f"  [{i}] {js.get_name()}")         # 名字，例如 Nintendo Switch Joy-Con (L/R)
        print(f"      轴数量 : {js.get_numaxes()}")      # 组合模式一般 6 个，单只一般 2 个
        print(f"      按键数 : {js.get_numbuttons()}")   # 组合模式 20 个
        print(f"      十字键 : {js.get_numhats()}")      # Joy-Con 没有真正十字键，通常 0
        print(f"      GUID  : {js.get_guid()}\n")        # 型号指纹，判断是不是同一设备很有用
    if "--list" in argv:                         # 只列清单模式
        return 0                                 # 打印完直接退出

    if count == 0:                               # 没设备就给出排查步骤
        print("没有检测到手柄，请检查：")          # 提示
        print("  1) 系统设置 → 蓝牙，看 Joy-Con 是否显示为已连接")     # 最常见原因
        print("  2) 没配对：按住 SYNC 键 3 秒，灯快闪后点连接")          # 配对方法
        print("  3) 连上后要重新运行脚本（SDL 只在启动时枚举一次设备）")   # 必须重启
        return 1                                 # 非 0 退出码表示失败

    index = 0                                    # 默认监控第 0 个手柄
    for a in argv:                               # 遍历参数
        if a.isdigit():                          # 是数字就当手柄编号
            index = int(a)                       # 转成整数
    index = min(index, count - 1)                # 防止越界，最多取到最后一个

    js = pygame.joystick.Joystick(index)         # 打开目标手柄
    js.init()                                    # 初始化，否则读不到数据
    print(f"正在监控 [{index}] {js.get_name()}")  # 告诉用户监控的是哪个
    print("拨动摇杆 / 按任意键，有变化才打印。Ctrl+C 退出\n")   # 操作说明

    prev_axes = None                             # 上一帧轴状态；None 保证第一帧必定打印
    prev_buttons = None                          # 上一帧按键状态
    use_events = True                            # 是否还尝试用事件队列检测断开
    t0 = time.time()                             # 起始时间，用于统计运行时长

    try:                                         # 包住循环，方便 Ctrl+C 优雅退出
        while True:                              # 主循环
            try:                                 # 手柄可能随时被拔掉
                pygame.event.pump()              # 让 SDL 读一次底层设备状态，这行不能省
            except pygame.error:                 # 底层报错一般意味着设备没了
                print("手柄已断开（pump 报错）")   # 明确提示
                return 1                         # 结束脚本

            if use_events:                       # 只在还没出过错时用事件接口
                try:                             # 事件接口在组合设备下可能抛内部错误
                    for event in pygame.event.get():                     # 取走事件队列
                        if event.type == pygame.JOYDEVICEREMOVED:        # 手柄断开事件
                            print("手柄已断开（收到移除事件）")            # 提示
                            return 1                                      # 结束脚本
                except (SystemError, KeyError, pygame.error):            # 捕获 SDL 内部异常
                    use_events = False           # 以后不再用事件接口
                    print("（SDL 事件队列异常，已切换为纯轮询模式，功能不受影响）")   # 告知用户

            try:                                 # 读的过程中设备可能消失
                axes_list = []                   # 先准备一个空列表，用来装每个轴的值
                for a in range(js.get_numaxes()):    # 按轴编号逐个遍历
                    axes_list.append(round(js.get_axis(a), 3))   # 读出一个轴的值并保留三位小数
                axes = tuple(axes_list)          # 转成元组，便于和上一帧整体比较

                buttons_list = []                # 准备空列表，用来装每个按键的状态
                for b in range(js.get_numbuttons()):         # 按按键编号逐个遍历
                    buttons_list.append(js.get_button(b))    # 读状态：0 表示松开，1 表示按下
                buttons = tuple(buttons_list)    # 转成元组，便于整体比较
            except pygame.error:                 # 读失败说明设备没了
                print("手柄已断开（读取失败）")    # 提示
                return 1                         # 结束

            if axes != prev_axes:                # 轴有变化才打印，避免刷屏
                pieces = []                      # 准备收集每一段 "轴名=值" 文字
                for a, v in enumerate(axes):     # 遍历所有轴
                    name = AXIS_LABELS.get(a, f"轴{a}")       # 取轴名，表里没有就用编号
                    pieces.append(f"{name}={v:+.3f}")         # 拼成 "轴名=±0.000"
                print("摇杆  " + "  ".join(pieces))           # 用两个空格连接后输出一行
                prev_axes = axes                 # 记住这一帧

            if buttons != prev_buttons:          # 按键有变化才打印
                pressed = []                     # 准备收集按下的键
                for i, v in enumerate(buttons):  # 遍历所有按键
                    if v:                        # 只处理按下的（值为 1）
                        name = BUTTON_LABELS.get(i, f"未知{i}")   # 取按键名
                        pressed.append(f"{name}({i})")            # 拼成 "名字(原始编号)"
                if pressed:                      # 确实有按下的键
                    print("按键  " + " + ".join(pressed))      # 用加号连接后输出
                else:                            # 全部松开了
                    print("按键  (全部松开)")     # 提示已复位
                prev_buttons = buttons           # 记住这一帧

            time.sleep(0.02)                     # 50Hz，够灵敏又不吃满 CPU
    except KeyboardInterrupt:                    # 用户按下 Ctrl+C
        print(f"\n已退出，共运行 {time.time() - t0:.1f} 秒")   # 友好的结束提示
        return 0                                 # 正常退出


if __name__ == "__main__":                        # 只在直接运行本文件时执行
    sys.exit(main())                              # 用 main() 的返回值作为退出码
