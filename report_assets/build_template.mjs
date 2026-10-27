import fs from 'node:fs/promises';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';
const wb = Workbook.create();
for (const name of ['商品別売上', '個人分配']) {
  const s = wb.worksheets.add(name);
  s.showGridLines = false;
  s.getRange('A1:H11').format.font = {name:'Arial',size:11};
  s.getRange('A1:H11').format.rowHeight = 26;
  s.getRange('A1:H11').format.columnWidth = 20;
  s.getRange('A1:A11').format.columnWidth = 30;
  s.getRange('B1:B11').format.columnWidth = 32;
  s.getRange('E1:E11').format.columnWidth = 28;
  s.getRange('A2').values = [[name]];
  s.getRange('A2').format.font = {name:'Arial',size:16,bold:true};
  s.getRange('A10:H10').values = [['学籍番号','作品名','販売単価（円）','材料費（円）','備考','セール','個数','売上（円）']];
  s.getRange('A10:H10').format = {fill:'#243B53',font:{name:'Arial',size:11,color:'#FFFFFF',bold:true},wrapText:true,rowHeight:34};
  s.getRange('A11:H11').values = [['0001','見本',200,50,'控除なし','なし',2,400]];
  s.getRange('A11').setNumberFormat('@');
  s.getRange('A11').format.horizontalAlignment = 'left';
  s.getRange('C11:D11').setNumberFormat('#,##0');
  s.getRange('G11:H11').setNumberFormat('#,##0');
  s.getRange('H11').formulas = [['=C11*G11']];
  s.freezePanes.freezeRows(10);
}
wb.recalculate();
const out = process.argv[2];
await (await SpreadsheetFile.exportXlsx(wb)).save(out);
console.log((await wb.inspect({kind:'region',sheetId:'商品別売上',range:'A10:H11',maxChars:1500})).ndjson);
for(const name of ['商品別売上','個人分配']) {
  const p = await wb.render({sheetName:name,range:'A1:H11',scale:1,format:'png'});
  await fs.writeFile(`${out}.${name}.png`,new Uint8Array(await p.arrayBuffer()));
}
