import fs from 'node:fs';
const resp = await fetch('http://localhost:5999/node_modules/.vite/deps/chunk-42N6TURS.js');
const text = await resp.text();
fs.writeFileSync('D:/ai大模型/DRAGON-AI-master/site/_shots/fastcrud-chunk.js', text);
console.log('bytes:', text.length);