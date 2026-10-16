import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
const base='https://lumira-mini-online.onrender.com';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const id=()=>randomBytes(16).toString('hex');
const room='QA'+randomBytes(4).toString('hex').toUpperCase();
const sellerKey=randomBytes(32).toString('hex'),buyerKey=randomBytes(32).toString('hex');
async function connect(name,key){
 for(let attempt=0;attempt<90;attempt++){
  try{
   const u=new URL('/ws',base);u.protocol='wss:';
   for(const [k,v] of Object.entries({room,name,session:key,v:'1',locations:'1'}))u.searchParams.set(k,v);
   const ws=new WebSocket(u);const waiting=new Map();
   const welcome=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{ws.close();reject(Error('welcome timeout'));},12000);
    ws.addEventListener('error',()=>{clearTimeout(timer);reject(Error('connection unavailable'));},{once:true});
    ws.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.type==='welcome'){clearTimeout(timer);resolve(m);}if(m.type==='trade')waiting.get(m.request)?.(m);});
   });
   assert.equal(welcome.trade,true);
   ws.send(JSON.stringify({type:'state',place:'town',x:0,z:2,fx:0,fz:1,sit:false}));
   return {ws,async request(action,extra={},request=id()){
    for(let n=0;n<10;n++){
     await sleep(350);
     const response=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{waiting.delete(request);reject(Error('trade timeout'));},15000);
      waiting.set(request,m=>{clearTimeout(timer);waiting.delete(request);resolve(m);});
      ws.send(JSON.stringify({type:'trade',request,action,...extra}));
     });
     if(['busy','storage_unavailable'].includes(response.code)){await sleep(1000);continue;}
     return response;
    }throw Error('trade retry limit');
   }};
  }catch{await sleep(3000);}
 }throw Error('server did not become available');
}
let seller,buyer;
try{
 seller=await connect('QA-Auction-Seller',sellerKey);
 const posted=await seller.request('post',{mode:'auction',kind:'item',id:'ore',qty:2,price:100,hours:24});
 assert.equal(posted.ok,true);assert.equal(posted.lot.net,140);assert.equal(posted.lot.fee,60);
 console.log('RESTART_WINDOW_READY: durable auction lot created; waiting for controlled server restart');
 await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('controlled restart not observed')),240000);
  seller.ws.addEventListener('close',()=>{clearTimeout(timer);resolve();},{once:true});
 });
 seller=await connect('QA-Auction-Seller',sellerKey);
 const mine=await seller.request('snapshot',{scope:'mine'});
 assert(mine.listings.some(l=>l.uid===posted.lot.uid));
 seller.ws.close();
 buyer=await connect('QA-Auction-Buyer',buyerKey);
 const bought=await buyer.request('buy',{lot:posted.lot.uid,amount:200});assert.equal(bought.ok,true);
 const items=(await buyer.request('snapshot')).mail.find(l=>l.lot===posted.lot.uid);
 assert.equal(items.attachment.id,'ore');assert.equal(items.attachment.qty,2);
 const request=id();const claim=await buyer.request('claim',{letter:items.uid},request);assert.equal(claim.ok,true);
 assert.equal((await buyer.request('claim',{letter:items.uid},request)).replayed,true);
 assert.equal((await buyer.request('claim',{letter:items.uid})).code,'letter_claimed');
 seller=await connect('QA-Auction-Seller',sellerKey);
 const money=(await seller.request('snapshot')).mail.find(l=>l.lot===posted.lot.uid);
 assert.equal(money.attachment.coins,140);
 assert.equal((await buyer.request('claim',{letter:money.uid})).code,'letter_missing');
 assert.equal((await seller.request('claim',{letter:money.uid})).ok,true);
 seller.ws.send(JSON.stringify({type:'state',place:'town',x:0,z:2,fx:0,fz:1,sit:true}));await sleep(500);
 const market=await seller.request('post',{mode:'market',seat:8,kind:'item',id:'herb',qty:1,price:100,hours:24});
 assert.equal(market.ok,true);assert.equal(market.lot.fee,0);
 assert.equal((await buyer.request('buy',{lot:market.lot.uid,amount:100})).ok,true);
 const proceeds=(await seller.request('snapshot')).mail.find(l=>l.lot===market.lot.uid);
 assert.equal(proceeds.attachment.coins,100);
 assert.equal((await seller.request('claim',{letter:proceeds.uid})).ok,true);
 const goods=(await buyer.request('snapshot')).mail.find(l=>l.lot===market.lot.uid);
 assert.equal((await buyer.request('claim',{letter:goods.uid})).ok,true);
 const cancelled=await seller.request('post',{mode:'auction',kind:'card',id:'brookit',qty:1,price:1,hours:24});
 assert.equal(cancelled.ok,true);assert.equal((await seller.request('cancel',{lot:cancelled.lot.uid})).ok,true);
 const returned=(await seller.request('snapshot')).mail.find(l=>l.lot===cancelled.lot.uid);
 assert.equal(returned.reason,'return');assert.equal(returned.attachment.id,'brookit');
 assert.equal((await seller.request('claim',{letter:returned.uid})).ok,true);
 assert.equal((await seller.request('snapshot',{scope:'mine'})).total,0);
 const health=await (await fetch(base+'/health')).json();assert(health.capabilities.includes('trade-v1'));
 console.log('PASS: real WSS + Postgres, lot survives server restart, offline seller, 30% fee, private mail, once-only claims, market 0%, cancel return; no open QA lots');
}finally{seller?.ws.close();buyer?.ws.close();}
