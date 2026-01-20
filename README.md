An attempt to rewrite GBIF Alert to make it work at EU scale (100M records).

# Design decisions:
- Use scaling best practices: see options at https://chatgpt.com/c/696e29e6-8458-832d-bdd6-a655be21b780 and https://docs.google.com/document/d/1hNUxaNzwVkjGSDHzqgzqunH5LhuV_8MtCKvUJrNzv10/edit?tab=t.0
- Use Wagtail as the embeded CMS
- Full SPA with Vue.js frontend (Vite as build tool)