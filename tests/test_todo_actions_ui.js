const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync('tools/universe_ui/app.js','utf8');
const start = source.indexOf('function pendingTodoState(');
const end = source.indexOf('async function deleteTodo(',start);
const stored = new Map(); let fail = null; const calls=[];
const todo={todo_id:'todo-1',project_id:'TEST',revision:2,state:'READY'};
const context={ state:{todos:[todo]}, elements:{todoFormError:{textContent:''}},
  crypto:{randomUUID:()=> 'same-request-id'},
  sessionStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},
  renderProjects(){},renderTodos(){},renderDetails(){},drawGraph(){},toast(){},
  invokeServerAction:async (id,request)=>{calls.push({id,request:JSON.parse(JSON.stringify(request))});
    if(id==='todo.state' && fail) throw fail;
    return id==='todo.read'? {todo:{...todo,revision:3,state:'DONE'}} : {result_propagation:{status:'RECORDED'}};
  }};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
(async()=>{
 await context.updateTodoState(todo,'DONE','',false);assert.equal(calls.length,0);
 fail={status:'NETWORK',message:'network'};
 await context.updateTodoState(todo,'DONE','test://passed',true);
 assert.equal(stored.size,1);const original=calls[0].request;
 fail=null;
 await context.updateTodoState({...todo,revision:99},'READY','',false);
 assert.deepEqual(calls[1].request,original);assert.equal(stored.size,0);
 assert.equal(calls[2].id,'todo.read');assert.equal(context.state.todos[0].state,'DONE');
 fail={status:409,message:'stale'};
 await context.updateTodoState(todo,'READY','',false);assert.equal(stored.size,0);
 const update=source.slice(source.indexOf('async function updateTodo('),start);
 assert.match(update,/invokeServerAction\("todo.update"/);assert.doesNotMatch(update,/method: "PATCH"/);
 assert.match(source,/invokeServerAction\("todo.create"/);
 console.log('Todo UI: completion evidence, shared Actions, uncertain replay and stale conflict passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
