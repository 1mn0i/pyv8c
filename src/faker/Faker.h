#ifndef FAKER_FAKER_H
#define FAKER_FAKER_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <fcntl.h>
#include <v8.h>

#include "tools.h"


class Faker
{
private:
	Faker() = delete;
	Faker(const Faker&) = delete;
	~Faker();
private:
	static void Print(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void Read(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void V8Version(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void RunJS(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void RunJSFile(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void DocumentAll(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void NativeWrapper(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void GetArg(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void SetArg(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void CreateConstructor(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void GetHook(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void SetHook(const v8::FunctionCallbackInfo<v8::Value>& info);
	static void StructuredClone(const v8::FunctionCallbackInfo<v8::Value>& info);

public:
	static void Register(v8::Isolate* isolate, v8::Local<v8::FunctionTemplate>& global_inteface_template);
};



#endif