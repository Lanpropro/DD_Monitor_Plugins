import {DurableObject} from "cloudflare:workers";
import {Match} from "./match.js";

const LOCAL_TTL = 180000;
const json = (value, status=200) => Response.json(value, {status, headers:{"Cache-Control":"no-store"}});
const error = (reason, status) => json({reason}, status);
const validOrigin = value => typeof value === "string" && /^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/.test(value);
function profile(value) {
  if (value == null) return null;
  if (typeof value !== "object" || Object.keys(value).some(key => !["platform","id","nickname","avatar"].includes(key)) ||
      typeof value.id !== "string" ||
      !value.id.length || value.id.length > 100 || /[\p{C}\p{Zl}\p{Zp}]/u.test(value.id) ||
      typeof value.platform !== "string" || !/^[a-zA-Z0-9_-]{1,40}$/.test(value.platform)) throw Error("invalid");
  const {nickname, avatar} = value;
  if ("nickname" in value && (typeof nickname !== "string" || !nickname.length ||
      [...nickname].length > 48 || /[\p{C}\p{Z}]/u.test(nickname.replaceAll(" ", "")))) throw Error("invalid");
  if ("avatar" in value && (typeof avatar !== "string" || avatar.length < 4 || avatar.length > 2048 ||
      avatar.length % 4 || !/^(?:[a-zA-Z0-9+/]{4})*(?:[a-zA-Z0-9+/]{2}==|[a-zA-Z0-9+/]{3}=)?$/.test(avatar))) throw Error("invalid");
  return {...value};
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/health") return json({service:"ddm-watch-directory", version:1});
    if (path === "/rooms" && request.method === "POST") {
      let args;
      try { args = await request.json(); } catch { return error("invalid",400); }
      if (!args || !["local","cloud"].includes(args.mode) || (args.mode==="local" && !validOrigin(args.origin)))
        return error("invalid",400);
      for (let i=0; i<8; i++) {
        const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1000000).padStart(6,"0");
        const stub = env.ROOMS.getByName(code);
        const reply = await stub.allocate(code, args.mode, args.mode==="local" ? args.origin : null);
        if (reply) return json(reply,201);
      }
      return error("busy",503);
    }
    const route = /^\/rooms\/(\d{6})(\/live)?$/.exec(path);
    if (!route) return error("room_missing",404);
    return env.ROOMS.getByName(route[1]).fetch(request);
  }
};

export class WatchRoom extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.match = new Match(Date.now());
    ctx.blockConcurrencyWhile(async () => { this.room = await ctx.storage.get("room"); });
  }
  sockets() { return this.ctx.getWebSockets().filter(ws=>ws.readyState===1); }
  seated() {
    return this.sockets().map(ws=>({ws,...ws.deserializeAttachment()})).filter(s=>Number.isInteger(s.side));
  }
  exists() {
    return this.room && (this.room.expires > Date.now() || (this.room.mode==="cloud" && this.seated().length));
  }
  async save() { await this.ctx.storage.put("room", this.room); }
  async allocate(code, mode, origin) {
    if (this.exists()) return null;
    const owner = crypto.randomUUID();
    this.room = {code, mode, origin, owner, revision:0, tokens:[null,null,null], expires:Date.now()+LOCAL_TTL};
    await this.save();
    await this.ctx.storage.setAlarm(this.room.expires);
    return {code,owner};
  }
  async alarm() {
    if (!this.exists()) { this.room = null; await this.ctx.storage.deleteAll(); }
    else if (this.room.expires > Date.now()) await this.ctx.storage.setAlarm(this.room.expires);
  }
  async fetch(request) {
    if (!this.exists()) return error("room_missing",404);
    const room = this.room;
    if (new URL(request.url).pathname.endsWith("/live")) {
      if (room.mode!=="cloud" || request.headers.get("Upgrade")?.toLowerCase()!=="websocket") return error("invalid",400);
      const [client,server] = Object.values(new WebSocketPair());
      if (this.sockets().length >= 8) return error("busy",503);
      this.ctx.acceptWebSocket(server);
      server.serializeAttachment({});
      setTimeout(() => {
        if (server.readyState === 1 && server.deserializeAttachment()?.side == null) server.close(1008,"join timeout");
      }, 10000);
      return new Response(null,{status:101,webSocket:client});
    }
    if (request.method==="GET") return json({mode:room.mode,origin:room.origin});
    if (request.headers.get("Authorization") !== "Bearer "+room.owner || room.mode!=="local") return error("forbidden",403);
    if (request.method==="DELETE") {
      this.room = null;
      await this.ctx.storage.deleteAll();
      return json({ok:true});
    }
    if (request.method==="PUT") {
      room.expires = Date.now()+LOCAL_TTL;
      await this.save();
      await this.ctx.storage.setAlarm(room.expires);
      return json({ok:true});
    }
    return error("invalid",405);
  }
  broadcast(data) {
    const body = JSON.stringify(data);
    for (const {ws} of this.seated()) { try { ws.send(body); } catch {} }
  }
  members() { return this.seated().sort((a,b)=>a.side-b.side).map(({side,profile})=>({side,profile})); }
  async changed() {
    this.room.revision++;
    this.match = new Match(Date.now());
    this.room.expires = Date.now()+LOCAL_TTL;
    await this.save();
    await this.ctx.storage.setAlarm(this.room.expires);
    this.broadcast({type:"state",revision:this.room.revision,members:this.members()});
  }
  async webSocketMessage(ws, raw) {
    const room = this.room;
    const seat = ws.deserializeAttachment();
    try {
      if (typeof raw!=="string" || raw.length>4096 || !room) throw Error("invalid");
      const d = JSON.parse(raw);
      if (d.type==="ping") { ws.send(JSON.stringify({type:"pong",server_ms:Date.now(),rich_profile:true})); return; }
      if (seat.side == null) {
        const members = this.members();
        let side;
        if (d.code!==room.code) throw Error("room_missing");
        if (d.type==="create" && d.host_key===room.owner && !room.tokens[0]) side=0;
        else if (d.type==="join") {
          if (!members.length) throw Error("host_offline");
          side = [1,2].find(i=>!members.some(m=>m.side===i));
          if (side==null) throw Error("room_full");
        } else if (d.type==="resume" && d.token && room.tokens.includes(d.token)) {
          side = room.tokens.indexOf(d.token);
          if (members.some(m=>m.side===side)) throw Error("seat_online");
        } else throw Error("unavailable");
        const identity = profile(d.profile);
        const token = d.type==="resume" ? d.token : crypto.randomUUID();
        room.tokens[side] = token;
        ws.serializeAttachment({side,profile:identity});
        ws.send(JSON.stringify({type:"seat",side,code:room.code,token}));
        await this.changed();
      } else if (d.type==="profile") {
        ws.serializeAttachment({...seat,profile:profile(d.profile)});
        this.broadcast({type:"members",revision:room.revision,members:this.members()});
      } else if (d.revision===room.revision && ["manual","reset","frames"].includes(d.type)) {
        let msg;
        if (d.type==="frames") msg=this.match.observe(seat.side,d,Date.now(),this.members().map(m=>m.side));
        else { this.match=new Match(Date.now()); msg={type:"measurement",lag:null,reset:true,manual:d.type==="manual"}; }
        if (msg) this.broadcast({...msg,revision:room.revision});
      }
    } catch (e) {
      if (ws.readyState !== WebSocket.OPEN) return;
      const known = ["room_missing","host_offline","room_full","seat_online","unavailable"];
      ws.send(JSON.stringify({type:"error",reason:known.includes(e.message)?e.message:"invalid"}));
      if (seat.side==null) ws.close(1000,"join failed");
    }
  }
  async webSocketClose(ws) {
    const seat = ws.deserializeAttachment();
    ws.serializeAttachment({});
    try { ws.close(1000); } catch {}
    if (this.room && seat.side!=null) await this.changed();
  }
  async webSocketError(ws) { await this.webSocketClose(ws); }
}
