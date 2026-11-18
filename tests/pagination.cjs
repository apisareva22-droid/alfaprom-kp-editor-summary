const { chromium } = require('playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const assert = require('node:assert/strict');
const zlib = require('node:zlib');
function zipText(data,name){
  const signature=Buffer.from([0x50,0x4b,0x01,0x02]);
  for(let pos=data.indexOf(signature);pos>=0;pos=data.indexOf(signature,pos+4)){
    const length=data.readUInt16LE(pos+28);
    if(data.subarray(pos+46,pos+46+length).toString()!==name)continue;
    const offset=data.readUInt32LE(pos+42),size=data.readUInt32LE(pos+20);
    const start=offset+30+data.readUInt16LE(offset+26)+data.readUInt16LE(offset+28);
    const bytes=data.subarray(start,start+size);
    return (data.readUInt16LE(pos+10)===8?zlib.inflateRawSync(bytes):bytes).toString();
  }
  throw Error('Missing ZIP entry '+name);
}
const repo = path.join(__dirname, '..');
const baseline = process.env.TEST_BASELINE_REF;
const html = baseline ? cp.execFileSync('git', ['show', baseline+':index.html'], {cwd:repo}) : fs.readFileSync(path.join(repo, 'index.html'));
const artifacts = process.env.TEST_ARTIFACTS_DIR;
if(artifacts)fs.mkdirSync(artifacts,{recursive:true});
const server = http.createServer((req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html)});
const testUrl = process.env.TEST_URL;
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser = await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL||'chrome',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1500,height:1000},acceptDownloads:true});
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
    page.on('dialog',async d=>{errors.push(d.message());await d.dismiss()});
    await page.goto(testUrl||`http://127.0.0.1:${server.address().port}`,{waitUntil:'networkidle'});
    console.log('libraries',await page.evaluate(()=>({docx:!!window.docx,canvas:!!window.html2canvas,pdf:!!window.jspdf})));
    // Проверяем путь пользователя: выделение в редакторе → кнопка → итоговая страница.
    for(const [command,selector,style,expected] of [
      ['bold','b,strong','fontWeight','700'],
      ['italic','i,em','fontStyle','italic'],
      ['underline','u','textDecorationLine','underline'],
      ['strikeThrough','s,strike','textDecorationLine','line-through']
    ]){
      const text='Проверка форматирования '+command;
      const editor=page.locator('.rich-editor').first();
      await editor.fill(text);
      await editor.press('ControlOrMeta+a');
      await page.locator('[data-rich="'+command+'"]').first().click();
      await page.waitForFunction(text=>document.querySelector('#previewPages .kp-description')?.textContent===text,text);
      await page.waitForFunction(()=>document.querySelector('#previewPages .kp-description')?.innerHTML===state.products[0].description);
      const actual=await page.locator('#previewPages .kp-description').first().evaluate((element,args)=>{
        const target=element.querySelector(args.selector);return target?getComputedStyle(target)[args.style]:null;
      },{selector,style});
      assert.equal(actual,expected,command+' reaches preview');
    }
    for(const [command,expected] of [['justifyCenter','center'],['justifyRight','right'],['justifyLeft','left']]){
      const editor=page.locator('.rich-editor').first();
      await editor.fill('Выравнивание '+expected);await editor.press('ControlOrMeta+a');
      await page.locator('[data-rich="'+command+'"]').first().click();
      await page.waitForFunction(expected=>document.querySelector('#previewPages .kp-description')?.textContent==='Выравнивание '+expected,expected);
      await page.waitForFunction(()=>document.querySelector('#previewPages .kp-description')?.innerHTML===state.products[0].description);
      assert.equal(await page.locator('#previewPages .kp-description [style]').first().evaluate(el=>getComputedStyle(el).textAlign),expected);
    }
    console.log('Toolbar formatting reaches preview');
    const inlineEditor=page.locator('.rich-editor').first();
    await inlineEditor.fill('Текст до фото и текст после фото');
    await inlineEditor.press('End');
    await page.locator('[data-inline-photo-add]').first().click();
    await page.waitForFunction(()=>/figure data-inline-photo-id/.test(state.products[0].description));
    assert.equal(await page.locator('.description-photo-empty').count(),1,'insert-photo button adds an editable photo block at the cursor');
    await page.locator('[data-inline-photo-choose]').click();
    await page.locator('[data-inline-photo-input]').setInputFiles({name:'inline.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLdyAAAAABJRU5ErkJggg==','base64')});
    await page.waitForFunction(()=>Object.keys(state.products[0].inlinePhotos||{}).length===1&&document.querySelector('.description-photo-editor img'));
    assert.equal(await page.locator('.description-photo-editor img').count(),1,'inline block accepts and renders its own image');
    console.log('Inline photo block inserts from the description toolbar');
    for (const [name,config] of Object.entries({
      empty:{},
      short:{text:60},
      'four-photos-long-description':{photos:4,text:250},
      'four-photos-tight-boundary':{photos:4,text:250,y:99},
      'long-description-lists-specs':{text:500,rows:90,lists:true},
      'nested-crop-multiple-products':{photos:2,text:150,rows:12,crop:true,multiple:true},
      'inline-photo-between-paragraphs':{text:80,inlinePhoto:true},
      'large-photo-layout':{photos:4,text:100,y:220},
      'formatted-description-across-pages':{photos:4,text:650,rich:true,rows:15},
      'long-formatted-word':{longWord:true}
    })) {
      const result=await page.evaluate(async config=>{
        await ensureDB();
        const p=createProduct();p.productName='Автоклав 300 литров';
        p.description=config.lists?'<p>Начало описания</p><ol><li>Первый пункт</li><li>Второй пункт</li></ol>':'';
        p.description+='<p>'+Array.from({length:config.text||0},(_,i)=>'слово'+i).join(' ')+'</p>';
        if(config.rich)p.description='<p style="text-align:center"><b>'+Array.from({length:config.text},(_,i)=>'слово'+i).join(' ')+'</b></p><p><i>Курсив</i> <u>Подчёркнуто</u> <s>Зачёркнуто</s><br>Новая строка</p><ol>'+Array.from({length:60},(_,i)=>'<li><b>Пункт '+i+'</b> '+('текст пункта '.repeat(i===10?350:5))+'</li>').join('')+'</ol><ul><li><i>Маркер списка</i></li></ul>';
        if(config.longWord)p.description='<p><strong>'+'А'.repeat(14000)+'</strong></p>';
        p.specsText=Array.from({length:config.rows||0},(_,i)=>'Параметр '+i+': Значение '+i).join('\n');
        p.price='100000';p.discountPrice='90000';p.note='Дополнительная информация';p.videoUrl='https://example.com/video';
        if(config.photos){
          const canvas=document.createElement('canvas');canvas.width=canvas.height=100;
          const ctx=canvas.getContext('2d');ctx.fillStyle='#f80';ctx.fillRect(0,0,100,100);
          const blob=await new Promise(r=>canvas.toBlob(r));
          for(let i=0;i<config.photos;i++){
            const id=await savePhoto(blob);p.photoIds.push(id);p.photoSizes[id]=82;
            p.photoPositions[id]={x:(i%2)*96,y:Math.floor(i/2)*(config.y||86)};
            if(config.crop)p.photoCrops[id]={top:5,bottom:5,left:5,right:5,applied:true,base:{top:10,bottom:10,left:10,right:10}};
          }
        }
        if(config.inlinePhoto){
          const canvas=document.createElement('canvas');canvas.width=canvas.height=100;
          const ctx=canvas.getContext('2d');ctx.fillStyle='#1687d3';ctx.fillRect(0,0,100,100);
          const blob=await new Promise(r=>canvas.toBlob(r));
          const inlineId='inline_test_photo',photoId=await savePhoto(blob);
          p.inlinePhotos={[inlineId]:photoId};p.photoSizes[inlineId]=58;
          p.description='<p>Текст до фотографии</p><figure data-inline-photo-id="'+inlineId+'"></figure><p>Текст после фотографии</p>';
        }
        state.products=[p];if(config.multiple)state.products.push({...createProduct(),productName:'Второе оборудование',description:'Описание второго оборудования',price:'500'});
        state.managerName='Тестовый менеджер';state.managerPhone='+7 000 000-00-00';
        const pages=await buildAllPages();
        document.getElementById('qa-pages')?.remove();
        const host=document.createElement('div');host.id='qa-pages';host.style.cssText='position:absolute;left:0;top:0;z-index:100;background:white';host.innerHTML=pages.join('');document.body.appendChild(host);
        await document.fonts.ready;
        const issues=[...host.children].flatMap((el,i)=>{
          const content=el.querySelector('.kp-content'),bottom=el.querySelector('.kp-bottom').getBoundingClientRect().top;
          return [...content.children,...content.querySelectorAll('.product-photo-frame')].filter(child=>child.getBoundingClientRect().bottom>bottom+1).map(child=>({page:i+1,block:child.className,overlap:child.getBoundingClientRect().bottom-bottom}));
        });
        const description=[...host.querySelectorAll('.kp-description')].map(el=>el.textContent).join('').replace(/\s+/g,' ').trim();
        const original=document.createElement('div');original.innerHTML=descriptionHtml(p.description);
        const expected=[original.textContent,...(config.multiple?['Описание второго оборудования']:[])].join('').replace(/\s+/g,' ').trim();
        let formattingPreserved=true;
        if(config.rich){
          const words=[...host.querySelectorAll('.kp-description p')].filter(el=>el.textContent.includes('слово'));
          formattingPreserved=words.length>1&&words.every(el=>el.style.textAlign==='center'&&el.querySelector('b'))&&!!host.querySelector('.kp-description i')&&!!host.querySelector('.kp-description u')&&!!host.querySelector('.kp-description s')&&!!host.querySelector('.kp-description ul');
          const numbers=[...host.querySelectorAll('.kp-description ol > li')].filter(el=>el.style.listStyleType!=='none').map(el=>el.value);
          formattingPreserved&&=JSON.stringify(numbers)===JSON.stringify(Array.from({length:60},(_,i)=>i+1));
        }
        const frames=[...host.querySelectorAll('.product-photo-frame')];
        const summary=host.querySelector('[data-order-summary]');
        const lastProduct=[...host.querySelectorAll('.kp-product-name')].at(-1);
        const summaryAfterProducts=!!summary&&!!lastProduct&&!!(lastProduct.compareDocumentPosition(summary)&Node.DOCUMENT_POSITION_FOLLOWING);
        const inlinePhoto=host.querySelector('.kp-inline-photo img');
        return {pages:pages.length,issues,textPreserved:description===expected,formattingPreserved,rows:host.querySelectorAll('.specs-table tbody tr').length,photos:frames.length,photosVisible:frames.every(frame=>{const r=frame.getBoundingClientRect();return r.width>1&&r.height>1}),inlinePhotoVisible:!config.inlinePhoto||!!inlinePhoto&&inlinePhoto.getBoundingClientRect().width>1&&inlinePhoto.getBoundingClientRect().height>1,summaryText:summary?.textContent.replace(/\s+/g,' ').trim()||'',summaryAfterProducts};
      },config);
      console.log(name,JSON.stringify(result));
      if(artifacts&&name==='four-photos-tight-boundary')await page.locator('#qa-pages > .kp-page').first().screenshot({path:path.join(artifacts,baseline?'before.png':'after.png')});
      if(artifacts&&name==='large-photo-layout')await page.locator('#qa-pages > .kp-page').first().screenshot({path:path.join(artifacts,'large-photos.png')});
      if(artifacts&&config.rich)await page.locator('#qa-pages > .kp-page').nth(1).screenshot({path:path.join(artifacts,'formatted-page.png')});
      if(artifacts&&config.multiple)await page.locator('#qa-pages > .kp-page').last().screenshot({path:path.join(artifacts,'equipment-summary.png')});
      assert.deepEqual(result.issues,[],name+' overlaps');
      assert.equal(result.textPreserved,true,name+' preserves description');
      assert.equal(result.rows,config.rows||0,name+' preserves rows');
      assert.equal(result.photos,config.photos||0,name+' preserves photos');
      assert.equal(result.photosVisible,true,name+' keeps photos visible');
      assert.equal(result.inlinePhotoVisible,true,name+' keeps inline description photo visible');
      assert.equal(result.formattingPreserved,true,name+' keeps formatting and list numbering');
      if(config.multiple){
        assert.match(result.summaryText,/Итог по оборудованию.*Автоклав 300 литров.*90\s?000.*Второе оборудование.*500.*Общая сумма.*90\s?500/s,name+' renders equipment total');
        assert.equal(result.summaryAfterProducts,true,name+' places total after all equipment');
      }else assert.equal(result.summaryText,'',name+' hides total for one product');
    }
    await page.evaluate(async()=>{
      document.getElementById('qa-pages').remove();
      const p=state.products[0];p.photoIds=p.photoIds.slice(0,2);
      p.photoIds.forEach((id,i)=>{p.photoSizes[id]=40;p.photoPositions[id]={x:i*45,y:0}});
      p.description='Короткое описание для проверки редактора';p.specsText='Объём: 300 л';
      state.products=[p];saveState();renderProductsEditor();await renderPreviewNow();
    });
    await page.locator('[data-field="productName"]').fill('Проверка автоклава');
    await page.waitForFunction(()=>document.querySelector('#previewPages .kp-product-name')?.textContent==='Проверка автоклава');
    await page.locator('[data-status="order"]').click();
    await page.locator('[data-vat-mode="without"]').click();
    await page.locator('#managerName').fill('Анна');
    await page.locator('.rich-editor').fill('Описание после редактирования');
    await page.waitForFunction(()=>document.querySelector('#previewPages .kp-description')?.textContent==='Описание после редактирования');
    assert.equal(await page.locator('#previewPages .price-vat').textContent(),'Цена без НДС');
    assert.equal(await page.locator('#previewPages .availability').textContent(),'Под заказ');
    await page.locator('#addProductBtn').click();
    assert.equal(await page.locator('.product-editor-card').count(),2);
    await page.reload({waitUntil:'networkidle'});
    assert.equal(await page.locator('.product-editor-card').count(),2,'products persist');
    assert.equal(await page.locator('#managerName').inputValue(),'Анна','manager persists');
    assert.equal(await page.locator('[data-field="productName"]').first().inputValue(),'Проверка автоклава','name persists');
    assert.equal(await page.locator('.rich-editor').first().textContent(),'Описание после редактирования','description persists');
    await page.waitForFunction(()=>window.docx&&window.html2canvas&&window.jspdf);
    const expectedPages=await page.evaluate(async()=>{
      // Экспортируем тот же пограничный случай с четырьмя фотографиями.
      const p=state.products[0],ids=p.photoIds.slice();
      p.photoIds=[...ids,...await Promise.all(ids.map(async id=>savePhoto(await getPhoto(id))))];
      p.photoIds.forEach((id,i)=>{p.photoSizes[id]=82;p.photoPositions[id]={x:(i%2)*96,y:Math.floor(i/2)*99}});
      p.description='<p style="text-align:center"><b>Жирный экспорт</b></p><p><i>Курсив экспорт</i> <u>Подчеркнуто экспорт</u> <s>Зачеркнуто экспорт</s></p><p><strong>'+Array.from({length:250},(_,i)=>'слово'+i).join(' ')+'</strong></p>';
      const second={...createProduct(),productName:'Второе оборудование для итога',price:'500',currency:'RUB'};
      state.products=[p,second];await renderPreviewNow();
      window.pdfFormattingChecks=[];
      const render=window.html2canvas;
      window.html2canvas=(element,options)=>render(element,{...options,onclone:async doc=>{
        await options.onclone?.(doc);
        const description=doc.querySelector('.pdf-render-root .kp-description b');
        if(description)window.pdfFormattingChecks.push(doc.defaultView.getComputedStyle(description).fontWeight);
      }});
      return (await buildAllPages()).length;
    });
    for(const [button,extension,signature] of [['#downloadPdfBtn','pdf','%PDF'],['#downloadDocxBtn','docx','PK']]){
      const pending=page.waitForEvent('download',{timeout:120000});
      await page.locator(button).click();
      const download=await pending;
      const filename=await download.path();
      assert.equal(await download.failure(),null,extension+' download succeeds');
      const data=fs.readFileSync(filename);
      assert.ok(data.subarray(0,4).toString().startsWith(signature),extension+' signature');
      assert.ok(data.length>1000,extension+' is not empty');
      if(extension==='pdf')assert.equal((data.toString('latin1').match(/\/Type \/Page\b/g)||[]).length,expectedPages,'PDF page count');
      if(extension==='pdf')assert.ok((await page.evaluate(()=>window.pdfFormattingChecks)).includes('700'),'PDF rasterizes bold description');
      if(extension==='docx'){
        const xml=zipText(data,'word/document.xml');
        assert.match(xml,/Итог по оборудованию/,'DOCX contains equipment total');
        assert.match(xml,/Второе оборудование для итога/,'DOCX contains second equipment in total');
        assert.match(xml,/90\s?500/,'DOCX contains total amount');
        const runs=xml.match(/<w:r>[\s\S]*?<\/w:r>/g)||[];
        for(const [text,style] of [['Жирный экспорт',/<w:b\b/],['Курсив экспорт',/<w:i\b/],['Подчеркнуто экспорт',/<w:u\b/],['Зачеркнуто экспорт',/<w:strike\b/]]){
          const run=runs.find(run=>run.includes(text));assert.ok(run&&style.test(run),'DOCX preserves '+text);
        }
        assert.ok((xml.match(/<w:p>[\s\S]*?<\/w:p>/g)||[]).some(p=>p.includes('Жирный экспорт')&&p.includes('w:val="center"')),'DOCX preserves alignment');
      }
      if(artifacts)await download.saveAs(path.join(artifacts,'export.'+extension));
      await page.waitForFunction(selector=>!document.querySelector(selector).disabled,button);
      console.log(extension+' export OK ('+data.length+' bytes)');
    }
    assert.deepEqual(errors,[],'runtime errors');
    console.log('Editor, persistence, PDF and DOCX checks passed');
  } finally {await browser.close();server.close()}
})().catch(e=>{console.error(e);server.close();process.exitCode=1});
