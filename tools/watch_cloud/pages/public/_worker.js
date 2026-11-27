export default {
  fetch(request, env) {
    return env.ROOM_SERVICE.fetch(request);
  }
};
