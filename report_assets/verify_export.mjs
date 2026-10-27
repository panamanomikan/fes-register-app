import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import {Workbook, FileBlob, SpreadsheetFile} from '@oai/artifact-tool';
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(process.argv[2]));
wb.recalculate();
const p = wb.worksheets.getItem('個人分配');
assert.deepEqual(p.getRange('B4:B8').values, [[202],[20],[180],[2],[3]]);
// A changed quantity must recalculate all downstream amounts.
const s = wb.worksheets.getItem('商品別売上');
s.getRange('G11').values = [[3]];
wb.recalculate();
assert.deepEqual(p.getRange('B4:B8').values, [[303],[30],[272],[1],[3]]);
s.getRange('G11').values = [[2]];
wb.recalculate();
for (const name of ['商品別売上','個人分配']) {
  const preview = await wb.render({sheetName:name,range:'A1:H13',scale:1,format:'png'});
  await fs.writeFile(`${process.argv[2]}.${name}.png`,new Uint8Array(await preview.arrayBuffer()));
}
console.log('PASS: Excel formulas recalculate revenue, personal payouts, group share and residual');
