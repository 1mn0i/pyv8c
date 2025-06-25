#ifndef FAKER_FAKER_H
#define FAKER_FAKER_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <fcntl.h>
#include <v8.h>

#include "tools.h"


using namespace v8;



class Faker
{
private:
	Faker() = delete;
	Faker(const Faker&) = delete;
	~Faker();
private:
	static void Print(const FunctionCallbackInfo<Value>& info);
	static void Read(const FunctionCallbackInfo<Value>& info);
	static void V8Version(const FunctionCallbackInfo<Value>& info);
	static void RunJS(const FunctionCallbackInfo<Value>& info);
	static void RunJSFile(const FunctionCallbackInfo<Value>& info);
	static void DocumentAll(const FunctionCallbackInfo<Value>& info);
	static void NativeWrapper(const FunctionCallbackInfo<Value>& info);
	static void GetArg(const FunctionCallbackInfo<Value>& info);
	static void SetArg(const FunctionCallbackInfo<Value>& info);
	static void CreateConstructor(const FunctionCallbackInfo<Value>& info);
	static void GetHook(const FunctionCallbackInfo<Value>& info);
	static void SetHook(const FunctionCallbackInfo<Value>& info);
	static void StructuredClone(const FunctionCallbackInfo<Value>& info);

public:
	static void Register(Isolate* isolate, Local<FunctionTemplate>& global_inteface_template);
};



#endif