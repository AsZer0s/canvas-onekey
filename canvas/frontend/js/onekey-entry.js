// 一键包：画布顶部中间放两个入口
// 「模型管理」回到自检 / 下载页（只在本机 127.0.0.1 打开）；
// 「姿势参考」打开原作者的姿势图网站。该站禁止批量下载和再分发，所以一键包不打包、不自动下载这些图，
// 只给入口：学员挑一张、单张保存，再拖进画布当参考图。
(function () {
  const pill = "padding:6px 12px;border-radius:99px;font:600 13px/1.4 system-ui,'Microsoft YaHei',sans-serif;" +
    "text-decoration:none;box-shadow:0 2px 8px rgba(0,0,0,.25);opacity:.85;color:#fff";
  function link(id, text, href, title, bg) {
    const a = document.createElement("a");
    a.id = id;
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = text;
    a.title = title;
    a.style.cssText = pill + ";background:" + bg;
    a.onmouseenter = () => (a.style.opacity = "1");
    a.onmouseleave = () => (a.style.opacity = ".85");
    return a;
  }
  function mount() {
    if (document.getElementById("onekey-entry")) return;
    const bar = document.createElement("div");
    bar.id = "onekey-entry";
    bar.style.cssText = "position:fixed;left:50%;top:14px;transform:translateX(-50%);z-index:9000;display:flex;gap:8px";
    bar.append(
      link("onekey-models", "模型管理", "http://127.0.0.1:10020/", "回到一键包管理页：自检显卡、安装或补装模型", "#0f766e"),
      link("onekey-poses", "姿势参考 ↗", "https://ai-pose-gallery.pages.dev/",
        "打开姿势参考图网站（外部网站）：挑一张单独保存，再拖进画布当参考图。该站禁止批量下载和再分发。", "#475569"),
    );
    document.body.appendChild(bar);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
