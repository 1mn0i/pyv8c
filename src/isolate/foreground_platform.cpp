#include "foreground_platform.h"
#include "foreground_runner.h"

ForegroundPlatform::ForegroundPlatform(v8::Platform* delegate)
    : delegate_(delegate), notifications_(std::make_shared<ForegroundNotifications>()) {}
ForegroundNotifications::Token ForegroundPlatform::token(v8::Isolate* isolate) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = isolates_.find(isolate);
    if (it != isolates_.end()) return it->second.token;
    auto token = notifications_->register_isolate();
    isolates_.emplace(isolate, State{token});
    return token;
}
void ForegroundPlatform::forget(v8::Isolate* isolate) {
    ForegroundNotifications::Token old = 0;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = isolates_.find(isolate);
        if (it != isolates_.end()) { old = it->second.token; isolates_.erase(it); }
    }
    if (old) notifications_->unregister_isolate(old);
}
int ForegroundPlatform::NumberOfWorkerThreads() { return delegate_->NumberOfWorkerThreads(); }
std::shared_ptr<v8::TaskRunner> ForegroundPlatform::GetForegroundTaskRunner(v8::Isolate* isolate, v8::TaskPriority priority) {
    auto id = token(isolate);
    return std::make_shared<ForegroundRunner>(delegate_->GetForegroundTaskRunner(isolate, priority), notifications_, id);
}
v8::PageAllocator* ForegroundPlatform::GetPageAllocator() { return delegate_->GetPageAllocator(); }
v8::ThreadIsolatedAllocator* ForegroundPlatform::GetThreadIsolatedAllocator() { return delegate_->GetThreadIsolatedAllocator(); }
size_t ForegroundPlatform::GetZeroSegmentSize() { return delegate_->GetZeroSegmentSize(); }
void ForegroundPlatform::OnCriticalMemoryPressure() { delegate_->OnCriticalMemoryPressure(); }
bool ForegroundPlatform::IdleTasksEnabled(v8::Isolate* isolate) { return delegate_->IdleTasksEnabled(isolate); }
double ForegroundPlatform::MonotonicallyIncreasingTime() { return delegate_->MonotonicallyIncreasingTime(); }
double ForegroundPlatform::CurrentClockTimeMillis() { return delegate_->CurrentClockTimeMillis(); }
int64_t ForegroundPlatform::CurrentClockTimeMilliseconds() { return delegate_->CurrentClockTimeMilliseconds(); }
double ForegroundPlatform::CurrentClockTimeMillisecondsHighResolution() { return delegate_->CurrentClockTimeMillisecondsHighResolution(); }
v8::Platform::StackTracePrinter ForegroundPlatform::GetStackTracePrinter() { return delegate_->GetStackTracePrinter(); }
v8::TracingController* ForegroundPlatform::GetTracingController() { return delegate_->GetTracingController(); }
void ForegroundPlatform::DumpWithoutCrashing() { delegate_->DumpWithoutCrashing(); }
v8::HighAllocationThroughputObserver* ForegroundPlatform::GetHighAllocationThroughputObserver() { return delegate_->GetHighAllocationThroughputObserver(); }
std::unique_ptr<v8::ScopedBoostablePriority> ForegroundPlatform::CreateBoostablePriorityScope() { return delegate_->CreateBoostablePriorityScope(); }
std::unique_ptr<v8::ScopedBlockingCall> ForegroundPlatform::CreateBlockingScope(v8::BlockingType type) { return delegate_->CreateBlockingScope(type); }
std::unique_ptr<v8::JobHandle> ForegroundPlatform::CreateJobImpl(v8::TaskPriority p, std::unique_ptr<v8::JobTask> task, const v8::SourceLocation& loc) { return delegate_->CreateJob(p, std::move(task), loc); }
void ForegroundPlatform::PostTaskOnWorkerThreadImpl(v8::TaskPriority p, std::unique_ptr<v8::Task> task, const v8::SourceLocation& loc) { delegate_->PostTaskOnWorkerThread(p, std::move(task), loc); }
void ForegroundPlatform::PostDelayedTaskOnWorkerThreadImpl(v8::TaskPriority p, std::unique_ptr<v8::Task> task, double delay, const v8::SourceLocation& loc) { delegate_->PostDelayedTaskOnWorkerThread(p, std::move(task), delay, loc); }
