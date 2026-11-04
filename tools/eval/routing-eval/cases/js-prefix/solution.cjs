exports.buildRequest = (instructions, tools, task) => ({
  prefix: JSON.stringify({instructions, tools, task}),
  messages: [{role: "user", content: task}]
});
