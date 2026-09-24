"use strict";
const { tools, curated } = window.DEMO_DATA;
const keys = ["purpose", "feature", "usage", "requirements"];
const labels = ["用途", "核心特点", "使用方式", "准备条件"];
const escape = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const selected = new Set();
let current = null;
let origin = null;
const status = (s) => {
  document.querySelector("#status").textContent = s;
};
function view(name) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== name));
  document
    .querySelectorAll("[data-view]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.view === name)),
    );
  status("");
}
document
  .querySelectorAll("[data-view]")
  .forEach((b) => b.addEventListener("click", () => view(b.dataset.view)));
document.querySelector("#cards").innerHTML = tools
  .map(
    (t, i) =>
      `<article class="card"><h3>${escape(t.name)}</h3><dl>${keys.map((k, j) => `<dt>${labels[j]}</dt><dd>${escape(t.fields[k].text)}</dd>`).join("")}</dl><a class="sources" href="${escape(t.url)}" target="_blank" rel="noopener noreferrer">查看 GitHub 来源 ↗</a><label><input type="checkbox" data-compare="${i}">加入比较</label><button class="primary" data-create="${i}">用这个工具创作 →</button></article>`,
  )
  .join("");
document.querySelectorAll("[data-compare]").forEach((b) =>
  b.addEventListener("change", () => {
    const i = Number(b.dataset.compare);
    b.checked ? selected.add(i) : selected.delete(i);
    const items = [...selected].map((i) => tools[i]);
    document.querySelector("#comparison").hidden = !items.length;
    document.querySelector("#compare-table").innerHTML =
      `<caption>四项资料对比 · ${items.length} 个工具</caption><thead><tr><th scope="col">比较项目</th>${items.map((t) => `<th scope="col">${escape(t.name)}</th>`).join("")}</tr></thead><tbody>${keys.map((k, j) => `<tr><th scope="row">${labels[j]}</th>${items.map((t) => `<td>${escape(t.fields[k].text)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  }),
);
function scriptText() {
  return [...document.querySelectorAll(".script-field textarea")]
    .map((n) => n.value.trim())
    .join("\n\n");
}
function updateCount() {
  document.querySelector("#count").textContent =
    `口播 ${[...scriptText().replace(/\s/g, "")].length} 字 · 时长需真实配音后确认`;
}
function sync() {
  document.querySelector("#copy").value =
    scriptText().replace(/\n\n/g, "\n") + "\n\n项目来源：" + current.url;
}
function create(i) {
  current = tools[i];
  document.querySelector("#script-title").textContent =
    current.name + " · 示例口播";
  document.querySelector("#empty").hidden = true;
  document.querySelector("#editor").hidden = false;
  const values = [
    "想把健身记录和训练过程整理得更清楚？",
    "每天分享好用的工具，今天讲" + current.name + "。",
    current.fields.purpose.text + current.fields.feature.text,
    current.fields.usage.text + current.fields.requirements.text,
  ];
  document.querySelector("#script-fields").innerHTML = [
    "痛点",
    "固定栏目句",
    "核心亮点",
    "使用示例与条件",
  ]
    .map(
      (l, j) =>
        `<div class="script-field"><label for="part-${j}">${l}</label><textarea id="part-${j}" rows="${j > 1 ? 4 : 2}">${escape(values[j])}</textarea></div>`,
    )
    .join("");
  document
    .querySelectorAll(".script-field textarea")
    .forEach((t) => t.addEventListener("input", updateCount));
  sync();
  updateCount();
  view("create");
  document.querySelector("#workbench").scrollIntoView();
}
document
  .querySelectorAll("[data-create]")
  .forEach((b) =>
    b.addEventListener("click", () => create(Number(b.dataset.create))),
  );
document
  .querySelector("#back")
  .addEventListener("click", () => view("discover"));
document.querySelector("#sync").addEventListener("click", () => {
  sync();
  status("已按当前口播更新文案。");
});
document.querySelector("#copy-script").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(scriptText());
    status("口播已复制。");
  } catch {
    status("浏览器未允许复制，请直接选中正文复制。");
  }
});
document.querySelector("#download").addEventListener("click", () => {
  const text =
    "AI-Hexing 示例文本（非实时 AI 生成）\n\n" +
    scriptText() +
    "\n\n发布文案\n" +
    document.querySelector("#copy").value;
  const url = URL.createObjectURL(
    new Blob([text], { type: "text/plain;charset=utf-8" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = "AI-Hexing-示例文本.txt";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  status("已生成示例文本下载。");
});
document.querySelector("#bot-grid").innerHTML = curated
  .map(
    (t, i) =>
      `<button class="bot" data-bot="${i}" aria-label="查看 ${escape(t.name)} 的介绍"><img src="${escape(t.avatar)}" alt="" loading="lazy"><span>${escape(t.name)}</span></button>`,
  )
  .join("");
const dialog = document.querySelector("#detail");
document.querySelectorAll("[data-bot]").forEach((b) =>
  b.addEventListener("click", () => {
    const t = curated[Number(b.dataset.bot)];
    origin = b;
    document.querySelector("#detail-title").textContent = t.name;
    document.querySelector("#detail-description").textContent = t.description;
    document.querySelector("#detail-highlight").textContent =
      t.highlights.join(" ");
    document.querySelector("#detail-link").href = t.github_url;
    dialog.showModal();
    document.querySelector("#detail-title").focus();
  }),
);
const close = () => {
  dialog.close();
  origin?.focus();
};
document.querySelector("#close").addEventListener("click", close);
dialog.addEventListener("cancel", (e) => {
  e.preventDefault();
  close();
});
dialog.addEventListener("click", (e) => {
  if (e.target === dialog) {
    const r = dialog.getBoundingClientRect();
    if (
      e.clientX < r.left ||
      e.clientX > r.right ||
      e.clientY < r.top ||
      e.clientY > r.bottom
    )
      close();
  }
});
const motion = document.querySelector("#motion");
motion.addEventListener("click", () => {
  const paused = document.querySelector("#bot-grid").classList.toggle("paused");
  motion.textContent = paused ? "继续漂浮" : "暂停漂浮";
  motion.setAttribute("aria-pressed", String(paused));
});
