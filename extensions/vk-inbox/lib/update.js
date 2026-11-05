// Compare against the manifest inside the exact public download, not a
// separately maintained version file that could drift from the archive.
(function (root) {
  const URL_DOWNLOAD = 'https://msg.bezrabotnyi.com/vk-userio-extension-mv3.zip';
  function compare(a, b) {
    const parse = value => /^\d+(?:\.\d+){0,3}$/.test(value) ? value.split('.').map(Number) : null;
    const left = parse(a), right = parse(b);
    if (!left || !right) throw new Error('Некорректный номер версии');
    for (let i=0; i<4; i++) { const d=(left[i] || 0)-(right[i] || 0); if(d) return Math.sign(d); }
    return 0;
  }
  async function manifestFromZip(buffer) {
    const view = new DataView(buffer), bytes = new Uint8Array(buffer);
    let end = -1;
    for (let i=bytes.length-22; i>=Math.max(0, bytes.length-65557); i--) {
      if (view.getUint32(i,true) === 0x06054b50) { end=i; break; }
    }
    if(end<0) throw new Error('Не удалось прочитать архив');
    const count=view.getUint16(end+10,true); let pos=view.getUint32(end+16,true);
    for(let i=0;i<count;i++) {
      if(pos+46>bytes.length || view.getUint32(pos,true)!==0x02014b50) throw new Error('Повреждён архив');
      const method=view.getUint16(pos+10,true), size=view.getUint32(pos+20,true), nameLen=view.getUint16(pos+28,true), extra=view.getUint16(pos+30,true), comment=view.getUint16(pos+32,true), local=view.getUint32(pos+42,true);
      const name=new TextDecoder().decode(bytes.subarray(pos+46,pos+46+nameLen));
      if(name==='manifest.json') {
        if(local+30>bytes.length || view.getUint32(local,true)!==0x04034b50 || size>1024*1024) throw new Error('Повреждён манифест');
        const start=local+30+view.getUint16(local+26,true)+view.getUint16(local+28,true);
        if(start+size>bytes.length) throw new Error('Архив неполон');
        const compressed=bytes.slice(start,start+size);
        let data;
        if(method===0) data=compressed;
        else if(method===8) data=new Uint8Array(await new Response(new Blob([compressed]).stream().pipeThrough(new DecompressionStream('deflate-raw'))).arrayBuffer());
        else throw new Error('Неподдерживаемый архив');
        if(data.length>1024*1024) throw new Error('Слишком большой манифест');
        return JSON.parse(new TextDecoder().decode(data));
      }
      pos+=46+nameLen+extra+comment;
    }
    throw new Error('Манифест не найден');
  }
  async function check() {
    const current=chrome.runtime.getManifest().version;
    try {
      const response=await fetch(URL_DOWNLOAD,{cache:'no-store',credentials:'omit'});
      if(!response.ok) throw new Error('HTTP '+response.status);
      const buffer=await response.arrayBuffer();
      if(buffer.byteLength>10*1024*1024) throw new Error('Слишком большой архив');
      const manifest=await manifestFromZip(buffer);
      if(manifest.manifest_version!==3 || manifest.name!=='Universal UserIO Agent' || typeof manifest.version!=='string') throw new Error('Неизвестный пакет');
      return {ok:true,current,latest:manifest.version,available:compare(manifest.version,current)>0,url:URL_DOWNLOAD};
    } catch(error) { return {ok:false,current,error:String(error.message || error)}; }
  }
  root.UserIOUpdate={check,compare,manifestFromZip};
})(typeof self !== 'undefined' ? self : this);
