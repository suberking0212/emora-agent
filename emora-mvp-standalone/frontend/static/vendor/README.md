# Local Rich Text Assets

These pinned browser assets are served locally; the UI does not fetch libraries from a CDN.

| Asset | Version | License |
| --- | --- | --- |
| Marked | 17.0.6 | MIT (`LICENSE.marked.md`) |
| DOMPurify | 3.3.3 | Apache-2.0 or MPL-2.0 (`LICENSE.dompurify`) |
| KaTeX | 0.16.47 | MIT (`katex/LICENSE`) |
| Mermaid | 11.17.2 | MIT (`LICENSE.mermaid`) |

The renderer supports GitHub-flavored Markdown, inline and display KaTeX delimiters, and Mermaid fenced diagrams. Raw HTML and Markdown images are removed. Mermaid uses strict security mode with HTML labels disabled; generated SVG is sanitized before insertion.
