Memory management specs for .cgitsync.
First redefinition of .cgitsync/state folder.

a project has his states (ie .gts) back-up in .cgitsync/state/hash.gts, where hash is sha256 of the GitTree content @timestamp. the  project/state/hash.gts is recorded in its ledeger with @timestamp.

For now they are locals. 

I wonder if it wouldn't be smart to back-up project-name/.cgitsync in a private/local "memory" repo. With an automated protocol, cgitsync should be capable of synchronizing local memory repos with its own private/distant global reference ledger that records all cgitsync admistrated pushed private/local project-name/.cgitsync

This is a totally new part of the project. As cli has its one src folder, i would suggest memory too