// Jalankan dengan Node.js; menguji fungsi JavaScript asli dari halaman.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
assert.match(html, /src="\/static\/js\/app.js(?:\?[^\"]*)?"/);
const source = fs.readFileSync(path.join(__dirname, '../web/static/js/app.js'), 'utf8');

function setup(outcomes) {
  const elements = new Map();
  function element() {
    return {value:'',disabled:false,dataset:{},classList:{add(){},remove(){}},
      setAttribute(){},appendChild(){},focus(){},textContent:'',innerHTML:''};
  }
  const sent=[];
  const context=vm.createContext({console,Math,Date,setTimeout(){},
    window:{addEventListener(){}},
    document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},
      createElement:element,querySelector(){return null},querySelectorAll(){return []}},
    async fetch(url, options){
      if(options?.body)sent.push(JSON.parse(options.body));
      const outcome=outcomes.shift();
      if(outcome instanceof Error)throw outcome;
      return {async json(){return outcome}};
    }});
  vm.runInContext(source, context);
  vm.runInContext('log=()=>{};toast=()=>{};selSeat="A1";newUUID();', context);
  vm.runInContext('document.getElementById("inpName").value="Pengujian"', context);
  return {context,elements,sent,run:code=>vm.runInContext(code,context)};
}

(async()=>{
  const rejected=setup([{success:false,booking_status:'REJECTED'}, {success:true,booking_id:'booking-2'}]);
  const firstId=rejected.elements.get('inpUUID').value;
  await rejected.run('doBook()');
  assert.notEqual(rejected.elements.get('inpUUID').value, firstId);
  rejected.run('selSeat="A2"');
  await rejected.run('doBook()');
  assert.notEqual(rejected.sent[0].request_id,rejected.sent[1].request_id);
  assert.equal(rejected.sent[1].seat_id,'SCH001-A2');

  for(const outcome of [new Error('koneksi putus'), {success:false,status_unknown:true,final:false}]){
    const uncertain=setup([outcome,{success:true,booking_id:'booking-1'}]);
    const id=uncertain.elements.get('inpUUID').value;
    await uncertain.run('doBook()');
    uncertain.run('selSeat="A2";newUUID()');
    uncertain.elements.get('inpName').value='Nama berubah';
    await uncertain.run('doBook()');
    assert.deepEqual(uncertain.sent[0],uncertain.sent[1]);
    assert.equal(uncertain.sent[1].request_id,id);
    assert.equal(uncertain.run('pendingBooking'),null);
  }
  const parallel=setup([{success:true,booking_id:'booking-1'}]);
  await Promise.all([parallel.run('doBook()'),parallel.run('doBook()')]);
  assert.equal(parallel.sent.length,1);
  const offline=setup([{success:false,error:'Server offline'}]);
  await offline.run('init()');
  assert.match(offline.elements.get('movieStrip').innerHTML,/Coba lagi/);
  assert.equal(offline.elements.get('connection').dataset.state,'error');
  const empty=setup([{success:true,schedules:[]}]);
  await empty.run('init()');
  assert.match(empty.elements.get('movieStrip').innerHTML,/Belum ada jadwal/);
  assert.equal(empty.elements.get('connection').dataset.state,'error');
  console.log('PASS: UUID setelah REJECTED, payload/ID retry, cegah klik ganda, error koneksi, jadwal kosong');
})().catch(error=>{console.error(error);process.exitCode=1});
