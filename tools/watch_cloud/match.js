// Same 512-bit fingerprint, ambiguity checks and group policy as watch_room_match.py.
const median = values => {
  const sorted = [...values].sort((a, b) => a - b);
  return (sorted[(sorted.length - 1) >> 1] + sorted[sorted.length >> 1]) / 2;
};
const spread = values => Math.max(...values) - Math.min(...values);
const consistent = (a, b) => spread(a.map((value, i) => value - b[i])) <= 140;

function distance(a, b) {
  let total = 0;
  for (let i = 0; i < 16 && total < 66; i++) {
    let v = a[i] ^ b[i];
    v -= (v >>> 1) & 0x55555555;
    v = (v & 0x33333333) + ((v >>> 2) & 0x33333333);
    total += (((v + (v >>> 4)) & 0x0f0f0f0f) * 0x01010101) >>> 24;
  }
  return total;
}

function pictureDelta(a, b, now) {
  if (a.length < 20 || b.length < 20 || Math.min(a.at(-1).at, b.at(-1).at) < now - 2200) return null;
  const pairs = new Map();
  for (const [recent, history] of [[a, b], [b, a]]) {
    for (let i = recent.length - 1; i >= 0 && recent[i].at >= now - 7500; i--) {
      let best = 66, at = 0;
      const candidates = [];
      for (const other of history) {
        const d = distance(recent[i].sig, other.sig);
        if (d >= 66) continue;
        candidates.push([d, other.at]);
        if (d < best) { best = d; at = other.at; }
      }
      if (best > 54 || candidates.some(([d, t]) => d < best + 12 && Math.abs(t - at) > 450)) continue;
      const pair = recent === a ? [recent[i].at, at] : [at, recent[i].at];
      pairs.set(pair.join(","), pair);
    }
  }
  if (pairs.size < 10) return null;
  const lag = median([...pairs.values()].map(([x,y]) => y-x));
  const good = [...pairs.values()].filter(([x,y]) => Math.abs(y-x-lag) <= 120).sort((p,q) => p[0]-q[0]);
  if (good.length < Math.max(10, .75*pairs.size) || good.at(-1)[0]-good[0][0] < 2400 ||
      Math.max(...good.at(-1)) < now-2000) return null;
  return Math.round(median(good.map(([x,y]) => y-x)));
}

export class Match {
  constructor(now) {
    this.frames = [[],[],[]];
    this.rtt = [0,0,0];
    this.basis = ["screen","screen","screen"];
    this.errors = [];
    this.candidates = [];
    this.verify = null;
    this.held = null;
    this.missing = null;
    this.locked = false;
    this.cutoff = now;
    this.next = now;
  }
  observe(side, data, now, seats) {
    if (this.locked || seats.length < 2) return;
    const {points, rtt, basis} = data;
    if (!Array.isArray(points) || points.length > 16 || !Number.isInteger(rtt) || rtt < 0 || rtt > 2000 ||
        !["screen","clock"].includes(basis)) throw Error("invalid");
    if (this.basis[side] !== basis) {
      this.candidates = []; this.verify = null; this.errors = []; this.frames[side] = [];
    }
    this.rtt[side] = rtt;
    this.basis[side] = basis;
    const frames = this.frames[side];
    for (const [at, hex] of points) {
      if (!Number.isSafeInteger(at) || Math.abs(at-now) > 8000 || typeof hex !== "string" ||
          !/^[0-9a-f]{128}$/.test(hex)) throw Error("invalid");
      if (at >= this.cutoff && (!frames.length || at >= frames.at(-1).at + 30)) {
        frames.push({at, sig: Uint32Array.from(hex.match(/.{8}/g), s => parseInt(s,16))});
      }
    }
    if (frames.length > 480) frames.splice(0, frames.length-480);
    if (now < Math.max(this.next, this.cutoff)) return;
    this.next = now+1600;
    const rtts = seats.map(i => this.rtt[i]);
    const msg = {type:"measurement", lag:null, samples:0, rtt:Math.max(...rtts), clock:seats.some(i=>this.basis[i]==="clock")};
    const offsets = [0, ...seats.slice(1).map(i => pictureDelta(this.frames[seats[0]], this.frames[i], now))];
    if (offsets.includes(null)) {
      this.candidates = [];
      this.missing ??= now;
      if (this.held !== null && now-this.missing < 12000) msg.held = this.held;
      else if (this.held !== null) Object.assign(this, new Match(now));
      return msg;
    }
    this.missing = null;
    msg.spread = spread(offsets);
    this.held = msg.lag = offsets.length === 2 ? offsets[1] : msg.spread;
    msg.leading = seats[offsets.indexOf(Math.min(...offsets))];
    this.errors.push(Math.abs(msg.lag));
    if (this.errors.length > 60) this.errors.shift();
    msg.samples = this.errors.length;
    msg.p95 = [...this.errors].sort((a,b)=>a-b)[Math.ceil(.95*msg.samples)-1];
    const tolerance = Math.max(350,rtts.reduce((a,b)=>a+b,0)/rtts.length+70);
    if (msg.spread <= tolerance) {
      this.candidates = [];
      if (this.verify && consistent(this.verify, offsets)) this.locked = msg.locked = true;
      else { this.verify = offsets; this.cutoff = now+1; msg.verifying = true; }
      this.frames = [[],[],[]];
    } else {
      this.verify = null;
      if (this.candidates.some(v => !consistent(v,offsets))) this.candidates = [];
      this.candidates.push(offsets);
      if (this.candidates.length > 3) this.candidates.shift();
      if (this.candidates.length === 3) {
        const times = offsets.map((_,i)=>median(this.candidates.map(v=>v[i])));
        const delays = {};
        seats.forEach((side,i)=>{
          if (this.candidates.every(v=>Math.max(...v)-v[i]>tolerance)) delays[side]=Math.max(...times)-times[i];
        });
        if (Object.keys(delays).length) {
          msg.delays = delays; msg.at = now+1500;
          if (seats.length===2) { msg.side=Number(Object.keys(delays)[0]); msg.adjust=delays[msg.side]; }
          Object.assign(this,new Match(msg.at+800));
        }
      }
    }
    return msg;
  }
}
