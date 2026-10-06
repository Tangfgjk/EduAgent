/* Zero-dependency contract tests: node scripts/simulation_analytics_test.cjs */
const assert = require('node:assert/strict');
const {analyze, probability} = require('../web/simulation-assets/analytics.js');
const sample = (turn, item_id, passed, extra={}) => ({turn,item_id,kc_id:'kc-a',action:'ANSWER',passed,hint_level:0,answer_exposed:false,estimated_mastery_after:.4,latent_mastery_after:.3,...extra});
const events = [sample(0,'item-a',false), sample(1,'item-a',true), sample(2,'item-b',true,{hint_level:1}),
  {turn:3,item_id:'item-c',action:'ASK_HINT'}, sample(4,'item-c',true), sample(5,'item-d',true,{answer_exposed:true}),
  sample(6,'item-e',true,{kc_id:'kc-b',estimated_mastery_after:0,latent_mastery_after:1}), {turn:7,action:'QUIT',passed:null}];
const input = JSON.stringify(events);
const result = analyze(events,{per_kc:{'kc-a':{mastery:.35},'kc-b':{mastery:1},'kc-only':{mastery:.1}}});
assert.equal(result.independentCount,2);
assert.equal(result.independentPassed,1);
assert.deepEqual(result.independent.map(p=>p.value),[0,.5]);
assert.equal(result.answerCount,6);
assert.equal(result.assistedCount,2);
assert.equal(result.hints.at(-1).value,2/6);
assert.equal(result.mastery.get('kc-a').length,5);
assert.deepEqual(result.perKC.find(row=>row.kc==='kc-b'),{kc:'kc-b',points:1,estimated:0,latent:1});
assert.deepEqual(result.perKC.find(row=>row.kc==='kc-only'),{kc:'kc-only',points:0,estimated:null,latent:.1});
assert.equal(JSON.stringify(events),input,'does not mutate raw events');
const empty=analyze([],{});
assert.equal(empty.independentCount,0); assert.deepEqual(empty.perKC,[]); assert.deepEqual(empty.hints,[]);
for(const value of [null,undefined,'0',NaN,Infinity,-.1,1.1])assert.equal(probability(value),false);
const invalid=analyze([sample(0,'x',true,{estimated_mastery_after:null,latent_mastery_after:Infinity})],{});
assert.equal(invalid.perKC[0].estimated,null);
assert.equal(invalid.mastery.get('kc-a')[0].latent,null);
console.log('Simulation analytics contract: passed (independent first encounter, missing/zero, assisted denominator, no mutation)');
