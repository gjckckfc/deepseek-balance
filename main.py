import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(HERE, "venv", "Scripts", "pythonw.exe")
CONFIG_PATH = os.path.join(HERE, "config.json")
HTML_PATH = os.path.join(HERE, "widget.html")


def ensure_venv():
    """如果当前不是 venv 里的 python，就重新用 venv 启动，保证依赖可用。"""
    if os.path.exists(VENV_PY) and os.path.abspath(sys.executable) != os.path.abspath(VENV_PY):
        try:
            os.execv(VENV_PY, [VENV_PY, __file__] + sys.argv[1:])
        except Exception:
            pass


def ensure_deps():
    try:
        import webview  # noqa: F401
    except ImportError:
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "pywebview", "requests"]
        )


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"api_key": "", "refresh_interval": 15}


def save_config_file(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class Api:
    """暴露给前端（widget.html）调用的接口。余额请求在服务端完成，规避浏览器跨域。"""

    def __init__(self):
        self.config = load_config()
        self._window = None

    def get_config(self):
        return {
            "api_key": self.config.get("api_key", ""),
            "refresh_interval": int(self.config.get("refresh_interval", 15)),
        }

    def save_config(self, api_key, refresh_interval):
        self.config["api_key"] = (api_key or "").strip()
        try:
            self.config["refresh_interval"] = max(5, int(refresh_interval))
        except Exception:
            self.config["refresh_interval"] = 15
        save_config_file(self.config)
        return self.get_config()

    def get_balance(self):
        import requests

        key = self.config.get("api_key", "")
        if not key:
            return {"ok": False, "need_key": True, "error": "未配置 API Key"}

        try:
            resp = requests.get(
                "https://api.deepseek.com/user/balance",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Accept": "application/json",
                },
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                infos = data.get("balance_infos", [])
                info = infos[0] if infos else {}
                return {"ok": True, "status": 200, "info": info, "count": len(infos)}
            return {
                "ok": False,
                "status": resp.status_code,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def minimize(self):
        try:
            if self._window:
                self._window.minimize()
        except Exception:
            pass

    def set_position(self, x, y):
        """由前端拖拽调用：把窗口移动到屏幕绝对坐标 (x, y)。"""
        try:
            if self._window:
                self._window.move(int(round(float(x))), int(round(float(y))))
        except Exception:
            pass

    def close(self):
        try:
            if self._window:
                self._window.destroy()
        except Exception:
            pass


def on_loaded(window):
    api = getattr(window, "_api", None)
    if api is not None:
        api._window = window
    try:
        import ctypes

        user32 = ctypes.windll.user32
        sw = user32.GetSystemMetrics(0)
        window.move(max(0, sw - 400), 40)
    except Exception:
        pass


def main():
    ensure_venv()
    ensure_deps()
    import webview

    api = Api()
    window = webview.create_window(
        title="DeepSeek 余额悬浮窗",
        url=HTML_PATH,
        js_api=api,
        width=360,
        height=320,
        frameless=True,
        on_top=True,
        transparent=True,
        easy_drag=False,
        text_select=False,
    )
    window._api = api
    webview.start(on_loaded, window)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        with open(os.path.join(HERE, "error.log"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
