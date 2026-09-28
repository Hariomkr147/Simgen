const APP={name:"Ester Lab",subtitle:"Class 12 · Carboxylic acids · Esterification mechanism",mark:"Es",accent:"#b0226b",accentDark:"#f06aad"};
const PRESETS={label:"Reactants",options:[
 {name:"Ethanoic acid + Ethanol",sub:"gives ethyl ethanoate",ico:"CH₃",R:"H₃C",Rp:"C₂H₅",acid:"Ethanoic acid",alc:"ethanol",ester:"Ethyl ethanoate",formula:"CH₃COOC₂H₅"},
 {name:"Methanoic acid + Methanol",sub:"gives methyl methanoate",ico:"H",R:"H",Rp:"CH₃",acid:"Methanoic acid",alc:"methanol",ester:"Methyl methanoate",formula:"HCOOCH₃"},
 {name:"Benzoic acid + Methanol",sub:"gives methyl benzoate",ico:"Ph",R:"C₆H₅",Rp:"CH₃",acid:"Benzoic acid",alc:"methanol",ester:"Methyl benzoate",formula:"C₆H₅COOCH₃"}]};
const CONTROLS=[];
const TOGGLE={label:"¹⁸O tag"};
const HUD=["Step","Charge","Change","¹⁸O is in"];
const STEPS=[
 {name:"Add H⁺",sub:"protonate C=O",do:"H⁺ from conc. H₂SO₄ adds to the <b>C=O oxygen</b>.",result:"Protonated acid",dur:2.3,
  ask:{q:"Why is the carbonyl oxygen protonated first?",o:["It makes the carbonyl carbon more positive, so the alcohol can attack it easily","It turns the acid into an alcohol","It removes water from the acid","It makes the acid a nucleophile"],a:0,
       e:"Protonation of the C=O oxygen pulls electrons away from the carbonyl carbon. This activates it for nucleophilic addition of the alcohol."}},
 {name:"Alcohol attacks",sub:"R′–OH adds to C",do:"The <b>alcohol oxygen</b> attacks the carbonyl carbon.",result:"Tetrahedral intermediate",dur:2.3,
  ask:{q:"In this step, the alcohol R′–OH acts as a…",o:["Electrophile","Nucleophile","Catalyst","Leaving group"],a:1,
       e:"The alcohol oxygen donates a lone pair to the electron-poor carbonyl carbon. A lone-pair donor that attacks a positive centre is a nucleophile."}},
 {name:"Proton shift",sub:"H⁺ moves to –OH",do:"A proton moves to an <b>–OH</b>, making <b>–⁺OH₂</b>.",result:"After proton transfer",dur:2.3,
  ask:{q:"Why does a proton move onto an –OH group?",o:["To make the carbon more negative","To form the ester directly","To turn –OH into –⁺OH₂, which is a better leaving group","To remove the alcohol"],a:2,
       e:"–OH is a poor leaving group. As –⁺OH₂ it can leave as a neutral water molecule, which is much easier."}},
 {name:"Lose H₂O",sub:"water leaves",do:"<b>–⁺OH₂ leaves as water</b>; C=O forms again.",result:"Protonated ester + water",dur:2.3,
  ask:{q:"The water molecule that leaves gets its oxygen from…",o:["The alcohol","The –OH group of the acid","The catalyst H₂SO₄","The air"],a:1,
       e:"The C–O bond that breaks is to the acid's –OH (now –⁺OH₂). Turn on the ¹⁸O tag: the alcohol's oxygen stays in the ester, not in the water."}},
 {name:"Lose H⁺",sub:"catalyst returned",do:"The protonated ester <b>gives back H⁺</b>.",result:"Ester formed!",dur:2.3,
  ask:{q:"What is the role of H⁺ (from conc. H₂SO₄) in this reaction?",o:["It is used up completely","It becomes part of the ester","It is the nucleophile","It is a catalyst: used in step 1 and given back in step 5"],a:3,
       e:"H⁺ is added in the first step and released in the last step. It speeds up the reaction but is not used up, so it is a catalyst."}}
];
const THINK=[
 {q:"Esterification is reversible. Why is water often removed (or the alcohol taken in excess) while making an ester?",o:["To make the acid stronger","To shift the equilibrium towards the ester","To stop the catalyst from working","To change the smell of the acid"],a:1,e:"Removing a product (water) or adding more of a reactant (alcohol) pushes the equilibrium forward, so more ester forms (Le Chatelier's principle)."},
 {q:"Propanoic acid is heated with methanol and a few drops of conc. H₂SO₄. The ester formed is…",o:["Propyl methanoate","Methyl propanoate","Methyl ethanoate","Propyl propanoate"],a:1,e:"The alkyl group of the alcohol (methyl) comes first and the acid part (propanoate) second: methyl propanoate, CH₃CH₂COOCH₃."},
 {q:"The esterification of a carboxylic acid with an alcohol is best described as…",o:["Electrophilic addition","Free-radical substitution","Nucleophilic acyl substitution","Elimination only"],a:2,e:"The alcohol (a nucleophile) attacks the acyl carbon, and the –OH of the acid is finally replaced by –OR′: nucleophilic acyl substitution."}
];
const WORDS=[
 {t:"Nucleophile",d:"A species with a lone pair that attacks an electron-poor atom. Here, the alcohol R′–OH."},
 {t:"Curly arrow",d:"Shows a pair of electrons moving: from a lone pair or bond to an atom or a new bond."},
 {t:"Leaving group",d:"The group that breaks away. –⁺OH₂ is a good leaving group because it leaves as neutral water."},
 {t:"Catalyst (H⁺)",d:"Conc. H₂SO₄ gives H⁺ in step 1 and gets it back in step 5."}
];
const FINISH={title:"Mechanism complete!",banner:"Ester formed!",html:"<p>1. H⁺ activates C=O &nbsp;2. Alcohol attacks &nbsp;3. Proton shift &nbsp;4. Water leaves &nbsp;5. H⁺ lost.</p><p style=\"margin-top:6px\">The <b>–OH of the acid</b> and the <b>H of the alcohol</b> leave as water. The ester keeps the alcohol's oxygen.</p>"};

// state names, HUD values and where the ¹⁸O label sits, for states 0..5
const SP=["Carboxylic acid + alcohol","Protonated acid","Tetrahedral intermediate","After proton transfer","Protonated ester + water","Ester + water"];
const CHG=["0","+1","+1","+1","+1","0"],CHL=["–","+H⁺","+R′OH","H⁺ moves","−H₂O","−H⁺"],O18=["alcohol","alcohol","C–O bond","C–O bond","ester","ester"];
// atom positions (bond-length units), bonds [a,b,order], formal charges, lone pairs [atom,angle] for each state
const ST=[
 {pos:{C:[0,0],R:[-1.15,0],O1:[.55,-.9],O2:[.55,.9],H2:[1.45,1.05],O3:[3.1,-.1],Rp:[4.25,-.1],H3:[3.1,.9],Hp:[1.9,-1.85]},
  bonds:[["R","C",1],["C","O1",2],["C","O2",1],["O2","H2",1],["O3","Rp",1],["O3","H3",1]],q:{Hp:1},lp:[["O1",-35],["O3",180]]},
 {pos:{C:[0,0],R:[-1.15,0],O1:[.55,-.9],Hp:[1.45,-1.3],O2:[.55,.9],H2:[1.45,1.05],O3:[2.35,-.05],Rp:[3.5,-.05],H3:[2.35,.95]},
  bonds:[["R","C",1],["C","O1",2],["O1","Hp",1],["C","O2",1],["O2","H2",1],["O3","Rp",1],["O3","H3",1]],q:{O1:1},lp:[["O3",180]]},
 {pos:{C:[0,0],R:[-1.15,0],O1:[0,-1.05],Hp:[.9,-1.5],O2:[0,1.05],H2:[-.85,1.55],O3:[1.15,0],Rp:[2.3,0],H3:[1.15,1.0]},
  bonds:[["R","C",1],["C","O1",1],["O1","Hp",1],["C","O2",1],["O2","H2",1],["C","O3",1],["O3","Rp",1],["O3","H3",1]],q:{O3:1},lp:[["O2",10],["O1",180]]},
 {pos:{C:[0,0],R:[-1.15,0],O1:[0,-1.05],Hp:[.9,-1.5],O2:[0,1.05],H2:[-.85,1.55],H3:[.85,1.55],O3:[1.15,0],Rp:[2.3,0]},
  bonds:[["R","C",1],["C","O1",1],["O1","Hp",1],["C","O2",1],["O2","H2",1],["O2","H3",1],["C","O3",1],["O3","Rp",1]],q:{O2:1},lp:[["O1",200]]},
 {pos:{C:[0,0],R:[-1.15,0],O1:[.55,-.9],Hp:[1.45,-1.3],O3:[.55,.9],Rp:[1.65,1.05],O2:[2.5,2.2],H2:[1.95,2.95],H3:[3.1,2.9]},
  bonds:[["R","C",1],["C","O1",2],["O1","Hp",1],["C","O3",1],["O3","Rp",1],["O2","H2",1],["O2","H3",1]],q:{O1:1},lp:[]},
 {pos:{C:[0,0],R:[-1.15,0],O1:[.55,-.9],Hp:[2.0,-1.9],O3:[.55,.9],Rp:[1.65,1.05],O2:[2.6,2.3],H2:[2.05,3.05],H3:[3.2,3.0]},
  bonds:[["R","C",1],["C","O1",2],["C","O3",1],["O3","Rp",1],["O2","H2",1],["O2","H3",1]],q:{Hp:1},lp:[]}
];
// curly arrows drawn during step i (from state i to i+1): from/to = lone pair, atom or bond
const ARR=[
 [{f:["lp","O1",-35],t:["atom","Hp"],b:1}],
 [{f:["lp","O3",180],t:["bondto","C","O3"],b:-1},{f:["bond","C","O1"],t:["atom","O1"],b:1}],
 [{f:["lp","O2",10],t:["atom","H3"],b:-1},{f:["bond","O3","H3"],t:["atom","O3"],b:1}],
 [{f:["bond","C","O2"],t:["atom","O2"],b:-1},{f:["lp","O1",200],t:["bond","C","O1"],b:-1}],
 [{f:["bond","O1","Hp"],t:["atom","O1"],b:1}]
];

let Lb=60,ox=0,oy=0,lay=null;
const pr=()=>PRESETS.options[S.preset];
function resetSim(){lay=null;}
// timeline inside a 2.3 s step: arrows grow 0-0.9 s and fade 1.0-1.5 s; atoms move 1.0-2.1 s; charges swap at 1.6 s
function curPos(){
  if(!anim)return ST[step].pos;
  const a=ST[anim.i].pos,b=ST[anim.i+1].pos,k=ease((anim.t-1.0)/1.1),out={};
  Object.keys(b).forEach(n=>{const p=a[n]||b[n],q=b[n];out[n]=[lerp(p[0],q[0],k),lerp(p[1],q[1],k)];});return out;
}
function layout(pos){
  const xs=[],ys=[];Object.values(pos).forEach(p=>{xs.push(p[0]);ys.push(p[1]);});
  const minx=Math.min(...xs)-.75,maxx=Math.max(...xs)+.8,miny=Math.min(...ys)-.7,maxy=Math.max(...ys)+.75;
  const L=Math.min(VIEW.w/(maxx-minx),VIEW.h/(maxy-miny),110);
  const t={L,ox:VIEW.x+(VIEW.w-(maxx-minx)*L)/2-minx*L,oy:VIEW.y+(VIEW.h-(maxy-miny)*L)/2-miny*L};
  if(!lay||lay.W!==W||lay.H!==H)lay={...t,W,H};else{lay.L+=(t.L-lay.L)*.12;lay.ox+=(t.ox-lay.ox)*.12;lay.oy+=(t.oy-lay.oy)*.12;}  // glide, don't jump
  Lb=lay.L;ox=lay.ox;oy=lay.oy;
}
const P=p=>[ox+p[0]*Lb,oy+p[1]*Lb];
function labelOf(n){if(n==="R")return pr().R;if(n==="Rp")return pr().Rp;if(n[0]==="O")return "O";return n==="C"?"C":"H";}
function colOf(n){if(n==="R"||n==="Rp")return "#1f6fd1";if(n[0]==="O")return "#d6247a";if(n==="Hp")return "#7a3fd1";return INK;}
function fontFor(n){return `${n==="R"||n==="Rp"?700:800} ${Math.round(clamp(Lb*.3,15,28))}px ${FONT}`;}
function radius(n){ctx.font=fontFor(n);return Math.max(ctx.measureText(labelOf(n)).width/2+4,Lb*.2);}
function bondLine(a,b,order,alpha,pos){
  const A=P(pos[a]),B=P(pos[b]),dx=B[0]-A[0],dy=B[1]-A[1],d=Math.hypot(dx,dy);if(d<1)return;
  const ux=dx/d,uy=dy/d,ra=radius(a),rb=radius(b),x1=A[0]+ux*ra,y1=A[1]+uy*ra,x2=B[0]-ux*rb,y2=B[1]-uy*rb;
  ctx.globalAlpha=alpha;ctx.strokeStyle=INK;ctx.lineWidth=Math.max(2,Lb*.04);ctx.lineCap="round";ctx.beginPath();
  if(order===2){const nx=-uy*Lb*.06,ny=ux*Lb*.06;ctx.moveTo(x1+nx,y1+ny);ctx.lineTo(x2+nx,y2+ny);ctx.moveTo(x1-nx,y1-ny);ctx.lineTo(x2-nx,y2-ny);}
  else{ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);}
  ctx.stroke();ctx.globalAlpha=1;
}
const bk=b=>[b[0],b[1]].sort().join("-");
function drawBonds(pos){
  if(!anim){ST[step].bonds.forEach(b=>bondLine(b[0],b[1],b[2],1,pos));return;}
  const mA={},mB={},k=ease((anim.t-1.0)/1.1);ST[anim.i].bonds.forEach(b=>mA[bk(b)]=b);ST[anim.i+1].bonds.forEach(b=>mB[bk(b)]=b);
  Object.keys({...mA,...mB}).forEach(key=>{const a=mA[key],b=mB[key];     // bonds that break fade out, new ones fade in
    if(a&&b){if(a[2]===b[2])bondLine(a[0],a[1],a[2],1,pos);else{bondLine(a[0],a[1],a[2],1-k,pos);bondLine(b[0],b[1],b[2],k,pos);}}
    else if(a)bondLine(a[0],a[1],a[2],1-k,pos);else bondLine(b[0],b[1],b[2],k,pos);});
}
function drawAtoms(pos){
  const q=anim?(anim.t>1.6?ST[anim.i+1].q:ST[anim.i].q):ST[step].q;
  Object.keys(pos).forEach(n=>{
    const [x,y]=P(pos[n]),lab=labelOf(n);ctx.font=fontFor(n);const w=ctx.measureText(lab).width;
    ctx.fillStyle="rgba(255,255,255,.92)";rr(x-w/2-3,y-Lb*.2,w+6,Lb*.4,6);ctx.fill();
    if(n==="O3"&&S.toggle){ctx.strokeStyle="#f5b301";ctx.lineWidth=3;ctx.beginPath();ctx.arc(x,y,Lb*.27,0,6.3);ctx.stroke();
      txt("18",x-w/2-1,y-Lb*.14,{size:Math.round(Lb*.18),weight:800,color:"#b98200",align:"right"});ctx.font=fontFor(n);}
    ctx.fillStyle=colOf(n);ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText(lab,x,y+1);ctx.textBaseline="alphabetic";
    if(q[n]){const cx=x+w/2+Lb*.1,cy=y-Lb*.22,r=Lb*.11;ctx.fillStyle="#d6247a";ctx.beginPath();ctx.arc(cx,cy,r,0,6.3);ctx.fill();   // (+) charge badge
      ctx.strokeStyle="#fff";ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(cx-r*.55,cy);ctx.lineTo(cx+r*.55,cy);ctx.moveTo(cx,cy-r*.55);ctx.lineTo(cx,cy+r*.55);ctx.stroke();}
  });
}
function lpPoint(pos,at,deg){const [x,y]=P(pos[at]),a=deg*Math.PI/180,r=Lb*.3;return [x+Math.cos(a)*r,y+Math.sin(a)*r,a];}
function drawLP(pos){
  if(anim&&anim.t>1.0)return;
  (anim?ST[anim.i].lp:ST[step].lp).forEach(([at,deg])=>{const [x,y,a]=lpPoint(pos,at,deg),nx=-Math.sin(a)*Lb*.06,ny=Math.cos(a)*Lb*.06;ctx.fillStyle=INK;
    [1,-1].forEach(s=>{ctx.beginPath();ctx.arc(x+nx*s,y+ny*s,Math.max(2,Lb*.035),0,6.3);ctx.fill();});});
}
function pt(pos,s){
  if(s[0]==="lp")return lpPoint(pos,s[1],s[2]).slice(0,2);
  if(s[0]==="atom")return P(pos[s[1]]);
  const A=P(pos[s[1]]),B=P(pos[s[2]]);return s[0]==="bond"?[(A[0]+B[0])/2,(A[1]+B[1])/2]:[lerp(A[0],B[0],.3),lerp(A[1],B[1],.3)];
}
function drawArrows(){
  if(!anim)return;const pos=ST[anim.i].pos;
  ctx.globalAlpha=anim.t>1.0?Math.max(0,1-(anim.t-1.0)/.5):1;
  ARR[anim.i].forEach(a=>{const p0=pt(pos,a.f),p1=pt(pos,a.t),d=Math.hypot(p1[0]-p0[0],p1[1]-p0[1])||1,ux=(p1[0]-p0[0])/d,uy=(p1[1]-p0[1])/d,s=Lb*.18;
    curly(p0[0]+ux*4,p0[1]+uy*4,p1[0]-ux*s,p1[1]-uy*s,a.b,Math.min(1,anim.t/.9),"#7a3fd1");});
  ctx.globalAlpha=1;
}
function draw(ctx,W,H){
  const pos=curPos();layout(pos);drawBonds(pos);drawLP(pos);drawAtoms(pos);drawArrows();
  const s=anim?anim.i:step;
  pill(s===0?pr().acid+" + "+pr().alc:s===5?pr().ester+" + water":SP[s]);
  if(step===5&&!anim)txt(pr().formula,W/2,H-50,{color:"#1f6fd1",size:13});
  if(step>=4||(anim&&anim.i===3&&anim.t>1.4)){const [x,y]=P(pos.O2);txt("water",x+Lb*.95,y+Lb*.35,{color:"#0e9aa7",align:"left"});}
  if(step===0&&!anim){const [x,y]=P(pos.Hp),[ax,ay]=P(pos.O3);
    txt("H⁺ (conc. H₂SO₄)",x,y-Lb*.38,{color:"#7a3fd1",size:12});txt("alcohol",ax+Lb*.55,ay-Lb*.42,{color:"#1f6fd1",size:12});}
  if(step===5&&!anim){const [x,y]=P(pos.Hp);txt("catalyst back",x,y-Lb*.32,{color:"#7a3fd1",size:12});}
}
function readouts(){const s=Math.min(step,5);return [s+"<small>/5</small>",CHG[s],CHL[s],S.toggle?O18[s]:"off"];}
