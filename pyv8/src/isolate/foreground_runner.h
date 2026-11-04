#ifndef PYV8_FOREGROUND_RUNNER_H
#define PYV8_FOREGROUND_RUNNER_H

#include "foreground_notification.h"
#include <v8-platform.h>

// Keeps the default runner's scheduling and nesting behavior. Tracking objects
// hold notification state independently of the lifetime of the Isolate.
class ForegroundRunner final : public v8::TaskRunner {
public:
    ForegroundRunner(std::shared_ptr<v8::TaskRunner> delegate,
                     std::shared_ptr<ForegroundNotifications> notifications,
                     ForegroundNotifications::Token token);
    bool IdleTasksEnabled() override;
    bool NonNestableTasksEnabled() const override;
    bool NonNestableDelayedTasksEnabled() const override;

protected:
    void PostTaskImpl(std::unique_ptr<v8::Task>, const v8::SourceLocation&) override;
    void PostNonNestableTaskImpl(std::unique_ptr<v8::Task>, const v8::SourceLocation&) override;
    void PostDelayedTaskImpl(std::unique_ptr<v8::Task>, double, const v8::SourceLocation&) override;
    void PostNonNestableDelayedTaskImpl(std::unique_ptr<v8::Task>, double, const v8::SourceLocation&) override;
    void PostIdleTaskImpl(std::unique_ptr<v8::IdleTask>, const v8::SourceLocation&) override;

private:
    std::unique_ptr<v8::Task> track(std::unique_ptr<v8::Task>, double delay);
    std::shared_ptr<v8::TaskRunner> delegate_;
    std::shared_ptr<ForegroundNotifications> notifications_;
    ForegroundNotifications::Token token_;
};

#endif
