Interview the user relentlessly until you reach a shared understanding. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled — the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the whole frontier in one round: number each question and give your recommended answer. Then wait for the user's answers before the next round.

Each question should be formatted like so:

```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Each round the user answers reshapes the tree — settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. When a frontier question needs a fact from the environment (filesystem, tools, etc.), dispatch a sub-agent to find it — don't ask the user for anything you could look up yourself. Don't block on it: a running exploration is an unsettled prerequisite, so only the questions downstream of it wait for the sub-agent to report — ask the rest of the frontier now. The _decisions_ are the user's — put each to them and wait.

The session is done when the frontier is empty: every branch of the design tree visited, nothing left silently assumed. Do not act on it until the user confirms you have reached a shared understanding.

Once you have completed, please update any specs you think needed and return a complete document of all the questions and answers

This following is the request context


These specs needs careful but significant adjustments made which will impact many aspects of 


1: It needs to be expanded to support locating artifacts which span multiple files.
Ie something like 
`{domain}/{content_kind}/{phase-or-any}/{file_index}`

Why:
One example of where this is needed is, High quality recording devices can have issues with storing files larger then (2^32)-1 bytes or 4 Gb. 
These types of devices will split files unto multiple files. Each file is a logical distinct file (its has its on valid headers,etc), whose sematic value is only value when considered as a whole. So the a-ref for these files are semantically the same, however these a need to have it point to more then one file

A stage would need to actively support this type of file to be supported, one such example would be to get audio preprocessing to merge, and reduce the file size as step.

2. The spec needs to be adjusted to redefine how an artifact is pointed to.
The new approach is that an artifact is universally identified by its CONTENT fingerprint. IE there no inclusion of its codex, version, providence, its primary key is only its CONTENT fingerprint (might be a need to rename this manifestation of the concept fingerprint to something like digest, index or identity)

Artifacts can also be located by providence fingerprint. This is essentially the same concept as the current fingerprint. Its some identifier which identifies how an artifact was derived. In the case of an input artifact this is just the CONTENT fingerprint.

Its important to therefore note that an artifact can be boxed. IE multiple fingerprints can point to the same artifact so long as they all have the same CONTENT fingerprint.

This change is also important because it means that an artifact is universally resolvable without an a-ref. A-ref would only be needed for understand how to programmatically consume an artifact. ie what codex to use, etc. In a sense a a-ref points to a schema

An `a-task` would still be useful as a way to resolve an artifact without knowing its CONTENT fingerprint.

An `a-instance` would then only represent a artifact in context of its schema, and wouldnt be needed for resolving an artifact from the cache.


