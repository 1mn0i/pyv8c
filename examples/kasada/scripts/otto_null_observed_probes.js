// Observed inputs to OTTO VM checks, captured before a one-item null write.
// These are isolated browser API probes, not decompiled VM functions: the VM's
// enclosing branch and final serialization are not reconstructed here.
(() => {
    function navigatorOptionalProperties() {
        // The VM reads browser-specific, optional Navigator properties.
        const names = ['brave', 'duckduckgo', 'globalPrivacyControl',
            'dateTimeString', 'mobileNetworkHash', 'rnd', 'rml', 'v', 'ln'];
        return Object.fromEntries(names.map(name => [name, typeof navigator[name]]));
    }

    function navigatorPluginsOwnDescriptor() {
        // The decoded instruction uses Object.getOwnPropertyDescriptor on navigator.
        const descriptor = Object.getOwnPropertyDescriptor(navigator, 'plugins');
        return {hasOwn: descriptor !== undefined, inherited: 'plugins' in navigator};
    }

    function touchEventCreation() {
        // The VM requests the legacy TouchEvent name and handles any exception.
        try {
            return {result: Object.prototype.toString.call(document.createEvent('TouchEvent'))};
        } catch (error) {
            return {error: error.name, message: error.message};
        }
    }

    function detachedSrcdocFrame() {
        // The VM creates a srcdoc iframe and tests whether the body contains it.
        const frame = document.createElement('iframe');
        frame.srcdoc = '1,2,3';
        return {contains: document.body.contains(frame),
            connected: frame.isConnected, contentWindow: frame.contentWindow === null};
    }

    return {navigatorOptionalProperties: navigatorOptionalProperties(),
        navigatorPluginsOwnDescriptor: navigatorPluginsOwnDescriptor(),
        touchEventCreation: touchEventCreation(),
        detachedSrcdocFrame: detachedSrcdocFrame()};
})()
