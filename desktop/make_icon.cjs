const fs = require("node:fs");
const path = require("node:path");
const {Map:mapIcon} = require("../workbench/node_modules/lucide");
const body=mapIcon[2].map(([tag,attrs])=>`<${tag} ${Object.entries(attrs).map(([k,v])=>`${k}="${v}"`).join(" ")}/>`).join("");
fs.mkdirSync(path.join(__dirname,"assets"),{recursive:true});
fs.writeFileSync(path.join(__dirname,"assets","icon.svg"),`<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><rect x="48" y="48" width="928" height="928" rx="200" fill="#17765f"/><g transform="translate(176 176) scale(28)" fill="none" stroke="#ffffff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${body}</g><circle cx="732" cy="702" r="92" fill="#edb750"/><circle cx="732" cy="702" r="33" fill="#17765f"/></svg>`);
