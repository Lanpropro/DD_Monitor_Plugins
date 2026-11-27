import assert from "node:assert/strict";
import gateway from "../tools/watch_cloud/pages/public/_worker.js";

for (const request of [
  new Request("https://watch.ddmonitor.top/rooms", {method:"POST", body:'{"mode":"cloud"}'}),
  new Request("https://watch.ddmonitor.top/rooms/123456/live", {headers:{Upgrade:"websocket"}}),
  new Request("https://watch.ddmonitor.top/rooms/123456", {method:"DELETE", headers:{Authorization:"Bearer fixture"}})
]) {
  const response = {status: request.headers.has("Upgrade") ? 101 : 200};
  const env = {ROOM_SERVICE:{fetch(forwarded) {
    assert.equal(forwarded, request);
    return Promise.resolve(response);
  }}};
  assert.equal(await gateway.fetch(request, env), response);
}
console.log("PASS: Pages preserves room requests, WebSocket upgrades and responses");
