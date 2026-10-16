exports.shouldRetry = (status, attempt) => status >= 400 && attempt <= 3;
