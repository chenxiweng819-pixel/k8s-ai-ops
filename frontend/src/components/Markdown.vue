<template>
  <div class="md-body" v-html="html"></div>
</template>

<script setup>
import { computed } from "vue";

// 轻量 Markdown 渲染：只支持 AI 回答里真正会用到的语法，
// 引入完整解析器（marked/markdown-it）对当前需求过重。
// 关键点：所有文本先做 HTML 转义，再插入受控标签，避免 XSS。
const props = defineProps({ text: { type: String, default: "" } });

function esc(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function inline(s) {
  return s
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/^\s*&gt;\s?(.*)$/gm, "<blockquote>$1</blockquote>");
}

const html = computed(() => {
  const src = props.text || "";
  const blocks = [];

  // 1. 先摘出代码块，避免内部内容被后续规则误伤
  let text = src.replace(/```(\w*)\n?([\s\S]*?)```/g, (_m, _lang, code) => {
    const i = blocks.length;
    blocks.push(`<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000CB${i}\u0000`;
  });

  text = esc(text);

  const lines = text.split("\n");
  const out = [];
  let inTable = false;
  let inList = false;

  const closeList = () => {
    if (inList) { out.push("</ul>"); inList = false; }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();

    // 表格
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const cells = line.split("|").slice(1, -1).map((c) => c.trim());
      if (/^[-:\s|]+$/.test(line)) continue; // 分隔行
      if (!inTable) {
        closeList();
        out.push('<table><thead><tr>' + cells.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>");
        inTable = true;
      } else {
        out.push("<tr>" + cells.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>");
      }
      continue;
    } else if (inTable) {
      out.push("</tbody></table>");
      inTable = false;
    }

    // 标题
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      closeList();
      const lvl = Math.min(h[1].length + 1, 6);
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
      continue;
    }

    // 列表
    const li = line.match(/^\s*([-*+]|\d+\.)\s+(.*)$/);
    if (li) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(li[2])}</li>`);
      continue;
    }
    closeList();

    // 分隔线
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) { out.push("<hr/>"); continue; }

    if (!line.trim()) { out.push(""); continue; }
    out.push(`<p>${inline(line)}</p>`);
  }

  if (inTable) out.push("</tbody></table>");
  closeList();

  // 2. 还原代码块
  return out.join("\n").replace(/\u0000CB(\d+)\u0000/g, (_m, i) => blocks[Number(i)] || "");
});
</script>
