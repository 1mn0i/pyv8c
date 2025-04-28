#ifndef FAKER_EVENTTARGET_H
#define FAKER_EVENTTARGET_H

#include <v8.h>
#include "../tools.h"

using namespace v8;


namespace faker {
	void EventTargetConstructor(const FunctionCallbackInfo<Value>& info);
	void AddEventListenerCallback(const FunctionCallbackInfo<Value>& info);
	void DispatchEventCallback(const FunctionCallbackInfo<Value>& info);
	void RemoveEventListenerCallback(const FunctionCallbackInfo<Value>& info);
} // namespace faker


#endif