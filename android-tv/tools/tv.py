#!/usr/bin/env python3
"""Android TV 的环境检查、构建、测试、安装和隔离演示入口（仅 Python 标准库）。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


PROJECT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT / "tools"
DEBUG_APK = PROJECT / "app/build/outputs/apk/debug/app-debug.apk"
DEBUG_ACTIVITY = "org.jzmedia.tv.debug/org.jzmedia.tv.MainActivity"


class ToolError(Exception):
    """An actionable error suitable for the command line, without a traceback."""


def executable_name(name):
    return name + ".exe" if sys.platform == "win32" else name


def executable(path):
    return path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK))


def run(command, *, env=None, capture=False, cwd=None):
    command = [str(part) for part in command]
    if not capture:
        print("运行：" + subprocess.list2cmdline(command), flush=True)
    result = subprocess.run(command, cwd=cwd or PROJECT, env=env, text=True,
                            errors="replace", capture_output=capture, check=False)
    if result.returncode:
        detail = ((result.stdout or "") + (result.stderr or "")).strip()
        raise ToolError(f"命令失败（退出码 {result.returncode}）：{subprocess.list2cmdline(command)}"
                        + (f"\n{detail}" if detail else "\n请查看上面的错误信息；首次构建需要联网下载 Gradle 和依赖。"))
    return result


def property_value(value):
    """Decode Java-properties escapes without corrupting literal UTF-8 characters."""
    def unescape(match):
        token = match[1]
        if token.startswith("u"):
            if len(token) != 5:
                raise ToolError("local.properties 的 sdk.dir 含无效 Unicode 转义；请用 Android Studio 重新设置 SDK 路径。")
            return chr(int(token[1:], 16))
        return {"n": "\n", "r": "\r", "t": "\t", "f": "\f"}.get(token, token)

    decoded = re.sub(r"\\(u[0-9a-fA-F]{4}|.)", unescape, value)
    try:
        # Java writes non-BMP characters as two UTF-16 surrogate escapes.
        return decoded.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
    except UnicodeDecodeError as error:
        raise ToolError("local.properties 的 sdk.dir 含不完整 Unicode 字符；请用 Android Studio 重新设置 SDK 路径。") from error


def sdk_location():
    """Match Gradle's local.properties priority; never rewrite the user's file."""
    properties = PROJECT / "local.properties"
    if properties.is_file():
        for line in properties.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*sdk\.dir\s*[=:]\s*(.*)$", line)
            if match:
                # Android Studio writes Java-properties escapes, e.g. C\:\\Users\\name.
                value = property_value(match[1])
                if not value:
                    raise ToolError("local.properties 的 sdk.dir 为空；请在 Android Studio 的 SDK Manager 中确认 SDK 路径。")
                path = Path(value).expanduser()
                return path if path.is_absolute() else PROJECT / path
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(key):
            return Path(os.environ[key]).expanduser()
    home = Path.home()
    candidates = [home / "Android/Sdk", home / "Android/sdk"]
    if sys.platform == "win32":
        candidates.insert(0, Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local"))) / "Android/Sdk")
    elif sys.platform == "darwin":
        candidates.insert(0, home / "Library/Android/sdk")
    return next((path for path in candidates if path.is_dir()), None)


def sdk_requirements():
    # The Android build file remains the single source for SDK versions.
    source = (PROJECT / "app/build.gradle.kts").read_text(encoding="utf-8")
    platform = re.search(r"\bcompileSdk\s*=\s*(\d+)", source)
    build_tools = re.search(r'\bbuildToolsVersion\s*=\s*"([^\"]+)"', source)
    if not platform or not build_tools:
        raise ToolError("无法读取 app/build.gradle.kts 的 SDK 版本；请同步更新 tools/tv.py 的版本读取规则。")
    return platform[1], build_tools[1]


def require_sdk():
    sdk = sdk_location()
    if sdk is None or not sdk.is_dir():
        raise ToolError("未找到 Android SDK" + (f"：{sdk}" if sdk else "")
                        + "。打开 Android Studio → SDK Manager 安装 SDK；将 SDK 路径设为 ANDROID_HOME，或由 Studio 写入 android-tv/local.properties。")
    platform, build_tools = sdk_requirements()
    missing = []
    if not (sdk / f"platforms/android-{platform}/android.jar").is_file():
        missing.append(f"Android SDK Platform {platform}")
    if not executable(sdk / "build-tools" / build_tools / executable_name("aapt2")):
        missing.append(f"Android SDK Build-Tools {build_tools}")
    if missing:
        raise ToolError("SDK 缺少：" + "、".join(missing)
                        + "。打开 SDK Manager：SDK Platforms 安装对应平台；SDK Tools 勾选 Show Package Details 后安装对应 Build-Tools。")
    return sdk.resolve()


def studio_java_homes():
    home = Path.home()
    if sys.platform == "win32":
        roots = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Android/Android Studio",
                 Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local"))) / "Programs/Android Studio"]
        return [root / "jbr" for root in roots]
    if sys.platform == "darwin":
        return [Path("/Applications/Android Studio.app/Contents/jbr/Contents/Home"),
                home / "Applications/Android Studio.app/Contents/jbr/Contents/Home"]
    return [Path("/opt/android-studio/jbr"), Path("/usr/local/android-studio/jbr"),
            home / "android-studio/jbr"]


def require_java():
    configured = os.environ.get("JAVA_HOME")
    candidates = [Path(configured).expanduser()] if configured else []
    if not configured:
        compiler = shutil.which(executable_name("javac"))
        if compiler:
            candidates.append(Path(compiler).resolve().parent.parent)
        candidates.extend(studio_java_homes())
    problems = []
    for home in candidates:
        java = home / "bin" / executable_name("java")
        javac = home / "bin" / executable_name("javac")
        if not executable(java) or not executable(javac):
            problems.append(f"{home} 中没有完整 JDK")
            continue
        result = subprocess.run([str(java), "-version"], capture_output=True, text=True,
                                errors="replace", timeout=15, check=False)
        version = re.search(r'version\s+"(?:1\.)?(\d+)', result.stdout + result.stderr)
        if result.returncode == 0 and version and 17 <= int(version[1]) <= 25:
            return home.resolve(), int(version[1])
        problems.append(f"{home} 的 Java 版本不适用于本工程 Gradle 9.1（需要 17–25）")
    detail = "；".join(problems) if configured else "未找到可用 JDK"
    raise ToolError(detail + "。推荐 JDK 17 或 21；可使用 Android Studio 自带的 jbr，将 JAVA_HOME 设为该目录（不要包含 bin），再重新运行命令。")


def build_environment():
    home, _ = require_java()
    sdk = require_sdk()
    env = os.environ.copy()
    env.update(JAVA_HOME=str(home), ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk))
    env["PATH"] = str(home / "bin") + os.pathsep + env.get("PATH", "")
    return env


def find_adb():
    sdk = sdk_location()
    candidate = sdk / "platform-tools" / executable_name("adb") if sdk else None
    if candidate and executable(candidate):
        return str(candidate.resolve())
    candidate = shutil.which(executable_name("adb"))
    if candidate:
        return candidate
    raise ToolError("未找到 adb。打开 Android Studio → SDK Manager → SDK Tools，安装 Android SDK Platform-Tools；也可把已有 adb 的目录加入 PATH。")


def doctor():
    failed = False
    for label, probe in (("JDK", require_java), ("Android SDK", require_sdk), ("adb", find_adb)):
        try:
            value = probe()
            if label == "JDK":
                value = f"{value[0]}（Java {value[1]}）"
            print(f"[通过] {label}：{value}")
        except (ToolError, OSError, subprocess.TimeoutExpired) as error:
            failed = True
            print(f"[待修复] {label}：{error}")
    if failed:
        raise ToolError("按上面的提示修复后，重新运行 doctor。")
    print("环境检查通过。可以运行 build、test 或 install；首次构建仍需联网下载依赖。")


def gradle(tasks, *, env, offline=False):
    wrapper = PROJECT / ("gradlew.bat" if sys.platform == "win32" else "gradlew")
    command = [str(wrapper)] + (["--offline"] if offline else []) + list(tasks)
    run(command, env=env)


def tool_tests():
    run([sys.executable, "-m", "unittest", "discover", "-s", TOOLS, "-p", "test_*.py"])
    run([sys.executable, PROJECT.parent / "scripts/check_docs_links.py", "--root", "android-tv"], cwd=PROJECT.parent)


def device_command(args):
    adb = find_adb()
    if args.command == "devices":
        run([adb, "devices", "-l"])
    elif args.command == "pair":
        # Let adb read its code interactively; never accept or print a code argument.
        run([adb, "pair", args.address])
    else:
        result = run([adb, "connect", args.address], capture=True)
        output = result.stdout + result.stderr
        if any(word in output.lower() for word in ("failed", "unable", "cannot", "error:")):
            raise ToolError(f"{output.strip()}\n请检查电视上的调试地址和端口；电脑与电视必须可互访，无线调试可能需要先运行 pair。")
        print(output.strip())


def choose_device(adb, serial=None):
    output = run([adb, "devices"], capture=True).stdout
    devices = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and not line.startswith(("List of devices", "*")):
            devices[fields[0]] = " ".join(fields[1:])
    if serial is None:
        if not devices:
            raise ToolError("未发现设备。请先启动 Android TV 模拟器，或连接已开启 ADB 调试的电视；在电视上允许此电脑调试。")
        if len(devices) > 1:
            choices = "、".join(f"{key}（{state}）" for key, state in devices.items())
            raise ToolError(f"发现多台设备：{choices}。请加 --serial 设备序列号，避免操作错误的电视。")
        serial = next(iter(devices))
    if serial not in devices:
        raise ToolError(f"未找到设备 {serial}。请确认设备已连接，可运行 adb devices 查看序列号。")
    state = devices[serial].split()[0]
    if state == "unauthorized":
        raise ToolError(f"设备 {serial} 未授权。请在电视/模拟器上允许此电脑的 USB/ADB 调试，然后重试。")
    if state != "device":
        raise ToolError(f"设备 {serial} 不可用（{devices[serial]}）。请重新连接或启动设备，并确认调试授权。")
    print(f"使用设备：{serial}")
    return serial


def install(args):
    apk = Path(args.apk).expanduser().resolve() if args.apk else DEBUG_APK
    if args.apk and (apk.suffix.lower() != ".apk" or not apk.is_file()):
        raise ToolError(f"未找到可安装的 APK：{apk}。--apk 需要指向已有的 .apk 文件。")
    adb = find_adb()
    serial = choose_device(adb, args.serial)
    if not args.apk:
        gradle([":app:assembleDebug"], env=build_environment(), offline=args.offline)
        if not apk.is_file():
            raise ToolError(f"构建后未找到 APK：{apk}")
    command = [adb, "-s", serial]
    try:
        result = run([*command, "install", "-r", apk], capture=True)
        if "Success" not in result.stdout:
            raise ToolError(result.stdout.strip() or "adb 未确认安装成功")
    except ToolError as error:
        raise ToolError(f"{error}\n安装未完成，不会自动卸载或降级现有应用。若提示签名冲突，请使用原签名构建；若提示 VERSION_DOWNGRADE，请使用更高版本。") from error
    if args.apk:
        print("APK 已安装。请从电视应用列表打开 jzmedia。")
        return
    result = run([*command, "shell", "am", "start", "-W", "-n", DEBUG_ACTIVITY], capture=True)
    output = result.stdout + result.stderr
    if "Error:" in output or "Exception" in output:
        raise ToolError(f"APK 已安装，但启动失败：{output.strip()}。请从电视应用列表打开 jzmedia。")
    print("Debug APK 已安装并启动。请在电视中填写 jzmedia 服务端地址。")


def find_ffmpeg(explicit=None):
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if executable(candidate):
            return str(candidate)
        raise ToolError(f"FFmpeg 路径不可执行：{candidate}。--ffmpeg 需要指向已有 ffmpeg 程序。")
    candidate = shutil.which(executable_name("ffmpeg"))
    if candidate:
        return candidate
    # Inspect files only: importing static_ffmpeg can trigger environment-specific setup.
    roots = {PROJECT.parent / ".venv", Path(sys.prefix)}
    patterns = ("lib/python*/site-packages/static_ffmpeg/bin/*/", "Lib/site-packages/static_ffmpeg/bin/*/")
    for root in sorted(roots):
        for pattern in patterns:
            for candidate in sorted(root.glob(pattern + executable_name("ffmpeg"))):
                if executable(candidate):
                    return str(candidate.resolve())
    raise ToolError("未找到 FFmpeg。请安装带 libx264/AAC 的 FFmpeg 并加入 PATH，或使用 demo --ffmpeg 程序绝对路径；不会自动下载。")


def demo(args):
    adb = find_adb()
    serial = choose_device(adb, args.serial)
    ffmpeg = find_ffmpeg(args.ffmpeg)
    command = [adb, "-s", serial]
    port = f"tcp:{args.port}"
    forwards = run([*command, "reverse", "--list"], capture=True).stdout
    if any(port == fields[1] for line in forwards.splitlines() if len(fields := line.split()) >= 3):
        raise ToolError(f"设备上的 {port} 已被转发占用。请使用 --port 18889 等其他端口；现有转发保持不变。")
    run([*command, "reverse", "--no-rebind", port, port], capture=True)
    try:
        server = [sys.executable, TOOLS / "smoke_server.py", "--port", str(args.port), "--ffmpeg", ffmpeg]
        for name in ("hls", "require_token", "browse_stress", "season_stress", "no_show_poster", "card_stress"):
            if getattr(args, name):
                server.append("--" + name.replace("_", "-"))
        print(f"正在生成合成媒体。出现服务端 URL 后，在电视中连接 http://127.0.0.1:{args.port}；按 Ctrl+C 结束并清理本次转发。", flush=True)
        if args.require_token:
            print("演示访问令牌：demo-tv-token（固定模拟值）", flush=True)
        run(server)
    finally:
        try:
            run([*command, "reverse", "--remove", port], capture=True)
        except (ToolError, OSError) as error:
            print(f"无法清理本次转发：{error}\n设备恢复连接后运行：{subprocess.list2cmdline([*command, 'reverse', '--remove', port])}", file=sys.stderr)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="检查 JDK、Android SDK 和 adb，给出修复提示")
    commands.add_parser("devices", help="列出已连接的电视和模拟器")
    for name, help_text in (("connect", "连接已开启网络 ADB 的电视"), ("pair", "按电视显示的配对地址交互输入配对码")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("address", help="电视显示的 IP:端口；配对与连接端口可能不同")
    for name, help_text in (("build", "构建本地 Debug APK"), ("test", "运行工具测试、JVM 单测和 Android lint"),
                            ("check", "运行 test 并构建 Debug / 未签名 Release"), ("install", "安装已有 APK；未指定 --apk 时构建并打开 Debug 应用"),
                            ("package", "导出带版本号的分发 APK、校验和与构建记录")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--offline", action="store_true", help="仅使用本地 Gradle 缓存；首次构建不要使用")
        if name == "install":
            command.add_argument("--serial", help="adb devices 中的设备序列号；多设备时必须指定")
            command.add_argument("--apk", help="安装指定 APK 并手动打开；不构建、不需要 JDK")
        if name == "package":
            command.add_argument("variant", choices=("debug", "release"))
    command = commands.add_parser("demo", help="连接设备并启动合成媒体演示；Ctrl+C 清理转发")
    command.add_argument("--serial", help="adb devices 中的设备序列号")
    command.add_argument("--port", type=int, default=18888, help="演示端口，默认 18888")
    command.add_argument("--ffmpeg", help="已有 FFmpeg 的路径；默认自动查找")
    for name, help_text in (("hls", "使用 HLS 播放"), ("require-token", "启用固定演示令牌 demo-tv-token"),
                            ("browse-stress", "120 集、合并集和多版本夹具"), ("season-stress", "八季及海报失败夹具"),
                            ("no-show-poster", "清空剧海报，检查占位图"), ("card-stress", "跨库重复、长标题与缺评分夹具")):
        command.add_argument("--" + name, action="store_true", help=help_text)
    args = parser.parse_args(argv)
    if args.command == "demo" and not 1 <= args.port <= 65535:
        parser.error("--port 必须是 1–65535")
    if args.command in ("connect", "pair"):
        match = re.fullmatch(r"[^\s/\-][^\s/]*:(\d+)", args.address)
        if not match or not 1 <= int(match[1]) <= 65535:
            parser.error("address 需要是电视显示的 IP:端口，例如 192.168.1.50:5555")
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.command == "doctor":
        doctor()
    elif args.command in ("devices", "connect", "pair"):
        device_command(args)
    elif args.command == "install":
        install(args)
    elif args.command == "demo":
        demo(args)
    else:
        env = build_environment()
        if args.command == "package":
            run([sys.executable, TOOLS / "package_apk.py", args.variant]
                + (["--offline"] if args.offline else []), env=env)
            return
        tasks = []
        if args.command in ("test", "check"):
            tool_tests()
            tasks.extend([":app:testDebugUnitTest", ":app:lintDebug"])
        if args.command in ("build", "check"):
            tasks.append(":app:assembleDebug")
        if args.command == "check":
            tasks.append(":app:assembleRelease")
        gradle(tasks, env=env, offline=args.offline)
        if args.command == "build":
            print(f"Debug APK：{DEBUG_APK}")
        else:
            print("检查通过。JVM 报告：app/build/reports/tests/testDebugUnitTest/index.html；lint 报告：app/build/reports/lint-results-debug.html（均在 android-tv 下）。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止。")
        raise SystemExit(130)
    except (ToolError, OSError, subprocess.TimeoutExpired) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1) from error
