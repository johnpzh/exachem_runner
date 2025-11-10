
// ----------
// Processes
// ----------
process test_retires {
    errorStrategy 'retry'
    maxRetries 6

    script:
    """
    #!/bin/bash
    echo "let's try."
    false
    """
}

// ---------
// Workflow
// ---------
workflow {
    test_retires()
}