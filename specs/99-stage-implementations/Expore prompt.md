
Ok, im stuggling with trying to figure out what the best ways to shape the contracts needed for resolving resources in between stages. Ie what does the contract presented to the stage look like. should the stage elect to require file on disk, or does ser-de abstract this? 

Can you produces a complete but as SIMPLE as possible implementation. I dont need all the bells and whistles, i just needs something which can demonstrate one possible solution to the current set of requirements. 
Ideally i need clarity around the input output contracts of resources going in and out of a stage, and how the serialisation  and deserialisation processes look like for intermediate, source and target artefacts. 


```

Stage {

	Inputs {}
	Outputs {}
	Artifacts {}


	run(??)
}

Stage1 {
// Some example which would mimink audio processing
// Should expects some large blob as input (the audio)
// Should output some small json object
// This object should represent an example of somthing im trying to solve, which is to enable 
// stages to be directly passed object instances between each over, without ser then de process. However this 
// output should still be written to the cache, for recoverability.
}

Stage2 {
// Some example which would mimink basic text processing
// Should expects some settings object
// Should take stage1's input 
// Should should output some basic json doc
}


]


Schedule {

	Stage get_next_stage()
}

Runner {
	Runner(MockSchedule, MockCache, ???)

}


Example:

runner = new Schedule() // Let this be some mock implementation which returns stage1 and then stage2

runner.run()

```